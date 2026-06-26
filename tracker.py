#!/usr/bin/env python3
"""Time Tracker — the always-on sampler.

Every SAMPLE_INTERVAL seconds it reads, via macOS built-ins (osascript/ioreg):
  • how long the keyboard/mouse has been idle,
  • the frontmost app,
  • and, if that app is a browser, the active tab's URL + title.

It writes one fine-grained row to the SQLite event log (db.events) per credited
interval. Everything the dashboard shows — timeline, categories, focus score,
trends — is derived from that log by analytics.py.

Two guards keep the data honest (learned the hard way from a focus-stealing
academic tab):
  • debounce  — a tab/app must be frontmost for two consecutive samples before
                it earns any time, so a page that steals focus for one tick
                (or a momentary app flicker) is never credited.
  • sleep-gap — if far more than one interval of wall-clock elapsed, the machine
                was asleep/suspended; we credit nothing for that gap.

On a cadence it also categorizes new domains/apps and pushes a daily summary to
GitHub. Stdlib + macOS only for tracking; the optional Claude categorization
uses the anthropic SDK if available.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from urllib.parse import urlsplit

import db

# ── Tunables ──────────────────────────────────────────────────────────────
SAMPLE_INTERVAL = 10           # seconds between readings (granular)
PUSH_INTERVAL = 300            # GitHub sync cadence (5 min)
CATEGORIZE_INTERVAL = 300      # categorize new keys every 5 min
IDLE_THRESHOLD = 120           # seconds of no input → stop counting
MAX_CREDIT = SAMPLE_INTERVAL * 3
DEBUG = bool(os.environ.get("TT_DEBUG"))

BROWSER_APPS = {
    "Google Chrome": "Chrome", "Arc": "Arc", "Safari": "Safari",
    "Brave Browser": "Brave", "Microsoft Edge": "Edge", "Dia": "Dia",
}

# Categories that trigger distraction alerts.
DISTRACTING = {"Social Media", "Entertainment"}

# Feature flags (overridable in config.json). All default on.
_CFG_CACHE = None


def cfg(key, default):
    global _CFG_CACHE
    if _CFG_CACHE is None:
        _CFG_CACHE = _load_config()
    return _CFG_CACHE.get(key, default)


# ── macOS introspection ─────────────────────────────────────────────────────

def _osa(script: str, timeout: float = 3.0) -> str | None:
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=timeout)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def idle_seconds() -> float:
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.rsplit("=", 1)[1].strip()) / 1_000_000_000
    except Exception:
        pass
    return 0.0


def frontmost_app() -> str | None:
    return _osa('tell application "System Events" to get name of first '
                'process whose frontmost is true')


def active_tab(app_name: str) -> tuple[str | None, str | None]:
    """(url, title) of the front window's active tab for a browser."""
    if app_name == "Safari":
        url = _osa('tell application "Safari" to get URL of current tab of front window')
        title = _osa('tell application "Safari" to get name of current tab of front window')
    else:
        url = _osa(f'tell application "{app_name}" to get URL of active tab of front window')
        title = _osa(f'tell application "{app_name}" to get title of active tab of front window')
    return (url or None), (title or None)


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).netloc.lower()
        if not host:
            return None
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return None


# ── GitHub sync (daily summary) ──────────────────────────────────────────────

def _load_config() -> dict:
    import json
    try:
        with open(os.path.join(os.path.dirname(__file__), "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def push_to_github(conn, date_str: str) -> bool:
    import base64
    import json
    import urllib.error
    import urllib.request
    import analytics

    cfg = _load_config()
    token = cfg.get("token") or os.environ.get("GITHUB_TOKEN")
    owner, repo = cfg.get("owner"), cfg.get("repo")
    folder = (cfg.get("folder") or "logs").strip("/")
    if not (token and owner and repo):
        return False

    summary = analytics.day_summary(conn, date_str)
    # Trim the timeline for the committed copy (keep it lightweight & private-ish).
    summary = {k: v for k, v in summary.items() if k != "timeline"}
    summary["generated"] = datetime.now().isoformat(timespec="seconds")
    content = base64.b64encode(json.dumps(summary, indent=2).encode()).decode()

    path = f"{folder}/{date_str}.json" if folder else f"{date_str}.json"
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json",
               "User-Agent": "time-tracker-app"}

    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers=headers), timeout=15) as r:
            sha = json.load(r).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[github] GET {e.code}")
    except Exception as e:
        print(f"[github] GET error: {e}")

    body = {"message": f"time-tracker: {date_str}", "content": content}
    if sha:
        body["sha"] = sha
    try:
        req = urllib.request.Request(api, data=json.dumps(body).encode(),
                                     headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except Exception as e:
        print(f"[github] PUT error: {e}")
        return False


# ── Native prompts / notifications ───────────────────────────────────────────

def _notify(title: str, text: str) -> None:
    """Fire-and-forget macOS notification."""
    safe = text.replace('"', "'")
    st = title.replace('"', "'")
    subprocess.Popen(["osascript", "-e",
                      f'display notification "{safe}" with title "{st}"'])


def _idle_prompt_async(gap_start: float, gap_end: float) -> None:
    """Ask, in a side thread, what the user was doing during an idle gap and
    store the answer as an annotation. Runs detached so it never blocks sampling."""
    import threading

    def worker():
        mins = round((gap_end - gap_start) / 60)
        prompt = (f"You were away for ~{mins} min. What were you working on?\\n"
                  f"(Leave blank to skip.)")
        script = (f'display dialog "{prompt}" default answer "" '
                  f'with title "Time Tracker" buttons {{"Skip","Save"}} '
                  f'default button "Save" with icon note giving up after 120')
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True)
        text = out.stdout.strip()
        if "button returned:Save" in text and "text returned:" in text:
            note = text.split("text returned:", 1)[1].strip()
            if note:
                c = db.connect()
                try:
                    db.add_annotation(c, gap_start, gap_end, note[:500])
                finally:
                    c.close()

    threading.Thread(target=worker, daemon=True).start()


def _maintenance_loop() -> None:
    """Background worker: categorize new keys, label sessions with Claude, and
    push the daily summary to GitHub — every CATEGORIZE_INTERVAL. Kept off the
    sampling thread so a slow network/Claude call never disturbs timing."""
    import threading

    def worker():
        conn = db.connect()
        while True:
            time.sleep(CATEGORIZE_INTERVAL)
            try:
                import categorize
                categorize.run()
            except Exception as e:
                print(f"[categorize] {e}")
            try:
                import sessions
                rep = sessions.run(conn)
                if rep.get("labeled"):
                    print(f"[sessions] labeled {rep['labeled']}")
            except Exception as e:
                print(f"[sessions] {e}")
            date_str = datetime.now().strftime("%Y-%m-%d")
            for _ in range(3):
                if push_to_github(conn, date_str):
                    db.set_meta(conn, "last_sync", str(time.time()))
                    break
                time.sleep(3)

    threading.Thread(target=worker, daemon=True).start()


# ── Main loop ────────────────────────────────────────────────────────────────

def run() -> None:
    print(f"[time-tracker] started · sample {SAMPLE_INTERVAL}s · "
          f"push {PUSH_INTERVAL}s · idle>{IDLE_THRESHOLD}s pauses")
    conn = db.connect()
    _maintenance_loop()  # categorize + label + sync, off-thread

    idle_prompt_on = cfg("idle_prompt", True)
    idle_prompt_min = float(cfg("idle_prompt_min_minutes", 5)) * 60
    distraction_on = cfg("distraction_alerts", True)
    distraction_limit = float(cfg("distraction_threshold_minutes", 30)) * 60

    last_sample = time.time()
    prev_key = None
    prev_idle = 0.0
    idle_started_at = None          # wall-clock when the current idle stretch began
    distract_secs = 0.0             # consecutive seconds on distracting categories
    distract_alerted = False

    while True:
        time.sleep(SAMPLE_INTERVAL)
        now = time.time()
        gap = now - last_sample
        last_sample = now

        idle = idle_seconds()
        app = frontmost_app()
        is_browser = bool(app and app in BROWSER_APPS)
        url = title = domain = None
        if is_browser:
            url, title = active_tab(app)
            domain = domain_of(url)
        cur_key = (app, domain) if app else None

        slept = gap > SAMPLE_INTERVAL * 1.6
        stable = cur_key is not None and cur_key == prev_key
        elapsed = min(gap, MAX_CREDIT)
        is_idle = idle > IDLE_THRESHOLD

        credited = False
        if app and stable and not slept and not is_idle:
            db.add_event(conn, ts=now - elapsed, dur=elapsed, app=app,
                         is_browser=is_browser, domain=domain, url=url, title=title)
            credited = True

        # ── Idle prompt: detect the transition from idle → active. ──
        if is_idle and idle_started_at is None and not slept:
            idle_started_at = now - idle      # idle began this long ago
        if idle_prompt_on and idle_started_at is not None and idle < 5 and not slept:
            gap_len = now - idle_started_at
            if gap_len >= idle_prompt_min:
                _idle_prompt_async(idle_started_at, now)
            idle_started_at = None

        # ── Distraction alert: consecutive time in a distracting category. ──
        if credited:
            key = domain if (is_browser and domain) else app
            cat = db.get_category(conn, key) if key else None
            if cat in DISTRACTING:
                distract_secs += elapsed
                if distraction_on and not distract_alerted and distract_secs >= distraction_limit:
                    _notify("Time Tracker",
                            f"{round(distract_secs/60)} min on {cat} this stretch.")
                    distract_alerted = True
            else:
                distract_secs = 0.0
                distract_alerted = False

        prev_key = cur_key
        prev_idle = idle

        if DEBUG:
            why = (f"+{elapsed:.0f}s→{domain or app}" if credited else
                   "slept" if slept else
                   "idle" if is_idle else
                   "debounce" if app and not stable else "-")
            with open(os.path.join(db.DATA_DIR, "samples.log"), "a") as fh:
                fh.write(f"{datetime.now():%H:%M:%S} gap={gap:5.0f} idle={idle:5.0f} "
                         f"app={app!r:22} dom={(domain or '')!r:22} {why}\n")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n[time-tracker] stopped")

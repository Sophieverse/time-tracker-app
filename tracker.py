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


# ── Main loop ────────────────────────────────────────────────────────────────

def run() -> None:
    print(f"[time-tracker] started · sample {SAMPLE_INTERVAL}s · "
          f"push {PUSH_INTERVAL}s · idle>{IDLE_THRESHOLD}s pauses")
    conn = db.connect()
    last_sample = time.time()
    last_push = 0.0
    last_cat = 0.0
    prev_key = None

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

        credited = False
        if app and stable and not slept and idle <= IDLE_THRESHOLD:
            # Credit the interval. Browser → store domain/url/title; native app
            # → just the app (the activity key is the app name itself).
            db.add_event(conn, ts=now - elapsed, dur=elapsed, app=app,
                         is_browser=is_browser, domain=domain, url=url, title=title)
            credited = True

        prev_key = cur_key

        if DEBUG:
            why = (f"+{elapsed:.0f}s→{domain or app}" if credited else
                   "slept" if slept else
                   "idle" if idle > IDLE_THRESHOLD else
                   "debounce" if app and not stable else "-")
            with open(os.path.join(db.DATA_DIR, "samples.log"), "a") as fh:
                fh.write(f"{datetime.now():%H:%M:%S} gap={gap:5.0f} idle={idle:5.0f} "
                         f"app={app!r:22} dom={(domain or '')!r:22} {why}\n")

        if now - last_cat >= CATEGORIZE_INTERVAL:
            last_cat = now
            try:
                import categorize
                categorize.run()
            except Exception as e:
                print(f"[categorize] {e}")

        if now - last_push >= PUSH_INTERVAL:
            last_push = now
            date_str = datetime.now().strftime("%Y-%m-%d")
            # Retry a few times: LaunchAgents occasionally can't resolve DNS for
            # a moment (resolver not yet attached to the process session).
            ok = False
            for attempt in range(3):
                if push_to_github(conn, date_str):
                    ok = True
                    break
                time.sleep(3)
            if ok:
                db.set_meta(conn, "last_sync", str(now))
                print(f"[time-tracker] synced {date_str} at {datetime.now():%H:%M}")
            else:
                print(f"[time-tracker] sync failed (will retry in {PUSH_INTERVAL}s)")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n[time-tracker] stopped")

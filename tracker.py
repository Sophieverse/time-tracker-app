#!/usr/bin/env python3
"""Time Tracker — a local macOS agent that logs time spent per website and
syncs a daily JSON file to a GitHub repo.

How it works
------------
Every SAMPLE_INTERVAL seconds it asks macOS:
  1. Is the keyboard/mouse idle?            (ioreg HIDIdleTime)  → if so, skip
  2. What app is frontmost?                 (System Events)
  3. If it's a browser, what's the active tab URL?  (AppleScript)

It credits the elapsed wall-clock time to that tab's domain, keeps a running
total in data/<date>.json, and every PUSH_INTERVAL seconds commits that file
to GitHub via the Contents API.

Everything uses only the Python standard library and macOS built-ins
(osascript, ioreg) — nothing to pip-install, nothing to compile.

The first time it tries to read a browser's tab, macOS shows the
"<App> wants access to control <Browser>" prompt. Click OK once per browser
and tracking begins. That prompt IS the browser-access request.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
CONFIG_PATH = os.path.join(HERE, "config.json")

# ── Tunables ──────────────────────────────────────────────────────────────
SAMPLE_INTERVAL = 15          # seconds between readings
PUSH_INTERVAL = 600           # seconds between GitHub pushes (10 min)
IDLE_THRESHOLD = 120          # seconds of no input → stop counting
MAX_CREDIT = SAMPLE_INTERVAL * 3  # cap one interval's credit (covers sleep/wake)

# Frontmost-app name → friendly browser label. These apps expose an
# active-tab URL via AppleScript; everything else is ignored.
BROWSER_APPS = {
    "Google Chrome": "Chrome",
    "Arc": "Arc",
    "Safari": "Safari",
    "Brave Browser": "Brave",
    "Microsoft Edge": "Edge",
    "Dia": "Dia",
}


# ── macOS introspection ─────────────────────────────────────────────────────

def _osa(script: str, timeout: float = 3.0) -> str | None:
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def idle_seconds() -> float:
    """Seconds since last keyboard/mouse input."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=3
        ).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.rsplit("=", 1)[1].strip()) / 1_000_000_000
    except Exception:
        pass
    return 0.0


def frontmost_app() -> str | None:
    return _osa(
        'tell application "System Events" to get name of first process whose frontmost is true'
    )


def active_tab_url(app_name: str) -> str | None:
    if app_name == "Safari":
        return _osa('tell application "Safari" to get URL of current tab of front window')
    return _osa(f'tell application "{app_name}" to get URL of active tab of front window')


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


# ── Local storage ───────────────────────────────────────────────────────────

def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def log_path(date: str) -> str:
    return os.path.join(DATA_DIR, f"{date}.json")


def load_day(date: str) -> dict:
    try:
        with open(log_path(date)) as f:
            return json.load(f).get("domains", {})
    except Exception:
        return {}


def save_day(date: str, domains: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "date": date,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "domains": dict(sorted(domains.items(), key=lambda kv: -kv[1])),
    }
    tmp = log_path(date) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, log_path(date))


# ── GitHub sync ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def push_to_github(date: str) -> bool:
    cfg = load_config()
    token = cfg.get("token") or os.environ.get("GITHUB_TOKEN")
    owner, repo = cfg.get("owner"), cfg.get("repo")
    folder = cfg.get("folder", "logs").strip("/")
    if not (token and owner and repo):
        return False

    path = f"{folder}/{date}.json" if folder else f"{date}.json"
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    try:
        with open(log_path(date), "rb") as f:
            raw = f.read()
    except Exception:
        return False
    content_b64 = base64.b64encode(raw).decode()

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "time-tracker-app",
    }

    # Fetch current SHA if the file already exists (required to update it).
    sha = None
    try:
        req = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            sha = json.load(r).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[github] GET failed: {e.code}")
    except Exception as e:
        print(f"[github] GET error: {e}")

    body = {"message": f"time-tracker: update {date}", "content": content_b64}
    if sha:
        body["sha"] = sha

    try:
        req = urllib.request.Request(
            api, data=json.dumps(body).encode(), headers=headers, method="PUT"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        print(f"[github] PUT failed: {e.code} {e.read().decode()[:200]}")
    except Exception as e:
        print(f"[github] PUT error: {e}")
    return False


# ── Main loop ────────────────────────────────────────────────────────────────

def run() -> None:
    print(f"[time-tracker] started, sampling every {SAMPLE_INTERVAL}s, "
          f"pushing every {PUSH_INTERVAL}s")
    date = today_key()
    domains = load_day(date)
    last_sample = time.time()
    last_push = 0.0

    while True:
        time.sleep(SAMPLE_INTERVAL)
        now = time.time()
        elapsed = min(now - last_sample, MAX_CREDIT)
        last_sample = now

        # Rollover at midnight: flush yesterday, start a fresh day.
        cur = today_key()
        if cur != date:
            save_day(date, domains)
            push_to_github(date)
            date, domains = cur, load_day(cur)

        if idle_seconds() <= IDLE_THRESHOLD:
            app = frontmost_app()
            label = BROWSER_APPS.get(app) if app else None
            if label:
                d = domain_of(active_tab_url(app))
                if d:
                    domains[d] = round(domains.get(d, 0) + elapsed, 1)
                    save_day(date, domains)

        if now - last_push >= PUSH_INTERVAL:
            ok = push_to_github(date)
            last_push = now
            if ok:
                print(f"[time-tracker] synced {date} "
                      f"({len(domains)} domains) at {datetime.now():%H:%M}")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n[time-tracker] stopped")

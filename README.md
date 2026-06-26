# Time Tracker (macOS app)

A local, automatic time tracker in the spirit of Rize — no browser extension.
A background agent samples your frontmost app (and the active browser tab's URL
+ title) every 10 seconds, stores a **granular event log in SQLite**, and a
local dashboard turns it into a Rize-style timeline, category breakdown, focus
score, top sites/apps, and weekly trends. A daily summary is synced to GitHub.

Tracking and analytics are stdlib + macOS only. AI categorization optionally
uses the `anthropic` SDK if available.

## Setup

```bash
cd ~/time-tracker-app
cp config.example.json config.json     # add GitHub token/owner/repo for sync
./install.sh                            # registers both LaunchAgents
open http://localhost:7799              # the dashboard
```

### Grant browser access

The first time the agent reads a browser tab, macOS shows
"…wants access to control <Browser>". Click **OK** once per browser (or enable
later under **System Settings → Privacy & Security → Automation**).

## How it works

```
tracker.py  ──sample every 10s──▶  db.py (SQLite: events)
   │  guards: debounce focus-steals, skip sleep gaps
   │  every 5 min ──▶ categorize.py (heuristics → Claude, cached)
   │  every 5 min ──▶ push daily summary to GitHub
   ▼
analytics.py  ──derives──▶  day summary · timeline · trends
   ▲
server.py  ──serves──▶  dashboard.html  (http://localhost:7799)
```

| File | Role |
|------|------|
| `tracker.py` | Always-on sampler → writes granular `events` rows; runs idle prompt + distraction alerts; background maintenance thread |
| `db.py` | SQLite schema + helpers (WAL, so read/write don't block) |
| `taxonomy.py` | Category list, colors, productivity flags, heuristic maps |
| `categorize.py` | Labels domains/apps: built-in heuristics, then Claude for the rest |
| `sessions.py` | Sessionizes blocks and AI-labels them from page titles ("Shopping for floor lamps") |
| `goals.py` | Daily focus goal + category limits; progress, streaks, 7-day strip |
| `analytics.py` | Day summary, sessionized timeline (with AI labels), weekly trends |
| `server.py` | JSON API (+ POST for goals/focus sessions) + serves the dashboard |
| `dashboard.html` | Single-page Rize-style UI (self-contained, no external requests) |
| `build_app.sh` | Builds the Dock-able "Time Tracker.app" launcher |
| `migrate_json.py` | One-off import of the old daily-totals JSON logs |

### Smart features

- **AI activity sessions** — contiguous blocks are labeled by Claude from the
  page/window titles you saw, so the day reads like a story ("Shopping for floor
  lamps · 20m"), not just a list of domains. Cached; needs an Anthropic key.
- **Goals & streaks** — set a daily focus-minute goal and per-category time
  limits; the dashboard shows progress rings, a streak, and a 7-day strip.
- **Focus timer** — a Pomodoro timer in the dashboard; completed sessions are
  logged.
- **Idle prompt** — after you return from being away >5 min, a native dialog
  asks what you were doing and stores it as an annotation. Toggle in config.json.
- **Distraction alerts** — a native notification after 30 min of continuous
  Social Media / Entertainment in one stretch. Toggle in config.json.

### Dock app

Run `./build_app.sh` to (re)build **/Applications/Time Tracker.app** — a launcher
with a custom icon that opens the dashboard in a clean window. Drag it onto your
Dock to pin it.

### Accuracy guards (learned the hard way)

- **Debounce** — a tab/app must be frontmost for two consecutive samples before
  it earns time, so a page that steals focus for a single tick is never counted.
- **Sleep-gap** — intervals spanning a suspend/wake gap are not credited.
- **Idle** — after 120s with no keyboard/mouse, counting pauses.

### Focus score

Each category is flagged productive or not (`taxonomy.py`). The focus score is
`productive_seconds / active_seconds × 100`.

## Dashboard

Always-on at **http://localhost:7799** (its own LaunchAgent). Shows: KPI cards,
a 24-hour timeline of category-colored blocks, a category donut, top sites and
apps, and a clickable 7-day trends chart. Refreshes every 15s when viewing today.

Run manually instead: `python3 server.py [port]`, or double-click
`dashboard.command`.

## GitHub sync

Every 5 minutes the agent commits a daily summary (totals, focus, category and
domain breakdowns — no timeline) to `{folder}/{date}.json` in the repo from
`config.json`. The raw SQLite DB stays local (it's in gitignored `data/`).

## Commands

```bash
tail -f data/tracker.log              # watch the agent
python3 categorize.py                 # force a categorization pass
python3 -c "import db,analytics,json; print(json.dumps(analytics.day_summary(db.connect(),'2026-06-26'),indent=2))"
./uninstall.sh                        # stop & remove both agents (data kept)
```

## config.json

```json
{ "token": "ghp_…", "owner": "Sophieverse", "repo": "time-tracker-logs", "folder": "logs" }
```

`config.json` and `data/` (the DB + logs) are gitignored.

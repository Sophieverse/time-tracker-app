# Time Tracker (macOS app)

A local background agent that logs how much time you spend on each website and
syncs a daily JSON file to a GitHub repo. No browser extension required — it
reads the active tab from **every** browser at once via macOS AppleScript.

Stdlib-only Python 3. Nothing to pip-install.

## Setup

```bash
cd ~/time-tracker-app
cp config.example.json config.json
# edit config.json → add your GitHub token, owner, repo, folder
./install.sh
```

`install.sh` registers a LaunchAgent that starts at login and restarts itself
if it crashes.

### Grant browser access

The first time the agent reads a browser's tab, macOS shows:

> "Time Tracker / python3" wants access to control "Google Chrome"

Click **OK** once per browser. (If you miss the prompt, enable it later under
**System Settings → Privacy & Security → Automation**.) This is the
browser-access request — it's how the app sees your URLs without an extension.

## How it works

| Step | Mechanism |
|------|-----------|
| Detect frontmost app | `System Events` via osascript |
| Read active tab URL (browsers only) | `tell application "<Browser>"` via osascript |
| Pause when you're away | `ioreg` HIDIdleTime (120s threshold) |
| Store totals | `data/<date>.json` (seconds per domain) |
| Sync | GitHub Contents API every 10 min, plus at midnight rollover |

Supported browsers: Safari, Chrome, Arc, Brave, Edge, Dia.

## Data format

`logs/2026-06-24.json` in your repo:

```json
{
  "date": "2026-06-24",
  "updated": "2026-06-24T14:32:10",
  "domains": {
    "github.com": 3612.0,
    "twitter.com": 847.0,
    "notion.so": 291.0
  }
}
```

Times are in seconds, sorted descending.

## Commands

```bash
tail -f data/tracker.log                  # watch it work
./uninstall.sh                            # stop & remove the agent
launchctl list | grep timetracker         # confirm it's running
```

## config.json

```json
{
  "token": "ghp_...",        // PAT with `repo` (or fine-grained contents:write) scope
  "owner": "Sophieverse",
  "repo":  "time-tracker-logs",
  "folder": "logs"
}
```

`config.json` and `data/` are gitignored so your token and raw logs never get
committed to *this* repo.

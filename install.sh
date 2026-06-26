#!/bin/bash
# Installs the Time Tracker as a macOS LaunchAgent so it runs automatically
# at login and restarts if it ever quits.
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$HERE/com.sophie.timetracker.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.sophie.timetracker.plist"

if [ ! -f "$HERE/config.json" ]; then
  echo "⚠️  No config.json found."
  echo "    Copy config.example.json → config.json and fill in your GitHub token."
  echo "    (You can still install now; sync will start once config.json exists.)"
fi

mkdir -p "$HERE/data"

# Substitute absolute paths into the tracker plist template.
sed -e "s|__TRACKER_PATH__|$HERE/tracker.py|g" \
    -e "s|__DATA_DIR__|$HERE/data|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Reload cleanly if already installed.
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

# Same for the dashboard agent (always-on viewer at http://localhost:7799).
DASH_SRC="$HERE/com.sophie.timetracker-dashboard.plist"
DASH_DST="$HOME/Library/LaunchAgents/com.sophie.timetracker-dashboard.plist"
sed -e "s|__DASH_PATH__|$HERE/dashboard.py|g" \
    -e "s|__DATA_DIR__|$HERE/data|g" \
    "$DASH_SRC" > "$DASH_DST"
launchctl unload "$DASH_DST" 2>/dev/null || true
launchctl load "$DASH_DST"

echo "✅ Time Tracker installed and running (tracker + dashboard at http://localhost:7799)."
echo ""
echo "Next: switch to each browser once so macOS shows the"
echo "      'wants access to control <Browser>' prompt — click OK."
echo ""
echo "Logs:    tail -f \"$HERE/data/tracker.log\""
echo "Stop:    launchctl unload \"$PLIST_DST\""
echo "Restart: launchctl load \"$PLIST_DST\""

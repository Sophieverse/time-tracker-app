#!/bin/bash
# Stops and removes the Time Tracker LaunchAgents (tracker + dashboard).
for label in com.sophie.timetracker com.sophie.timetracker-dashboard; do
  PLIST_DST="$HOME/Library/LaunchAgents/$label.plist"
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
done
echo "✅ Time Tracker uninstalled (tracker + dashboard). Your data/ logs are untouched."

#!/bin/bash
# Stops and removes the Time Tracker LaunchAgent.
PLIST_DST="$HOME/Library/LaunchAgents/com.sophie.timetracker.plist"
launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f "$PLIST_DST"
echo "✅ Time Tracker uninstalled. Your data/ logs are untouched."

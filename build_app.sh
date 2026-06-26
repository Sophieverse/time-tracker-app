#!/bin/bash
# Builds "Time Tracker.app" into /Applications — a Dock-able launcher that opens
# the dashboard (http://localhost:7799) in a clean Chrome app-mode window,
# starting the server's LaunchAgent first if needed.
#
# Uses only built-in macOS tools (Chrome headless for the icon render, sips,
# iconutil). Re-run any time to rebuild.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Time Tracker.app"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 1. Render the icon source to a 1024px PNG.
"$CHROME" --headless=new --disable-gpu --default-background-color=00000000 \
  --force-device-scale-factor=1 --window-size=1024,1024 --hide-scrollbars \
  --screenshot="$HERE/.icon.png" "$HERE/icon.html" 2>/dev/null
sleep 1

# 2. Assemble the multi-resolution iconset → .icns.
ICONSET="$HERE/.AppIcon.iconset"; rm -rf "$ICONSET"; mkdir "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$HERE/.icon.png" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
  d=$((sz*2))
  sips -z $d $d "$HERE/.icon.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$HERE/.AppIcon.icns"

# 3. Build the bundle.
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/.AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>Time Tracker</string>
  <key>CFBundleDisplayName</key>     <string>Time Tracker</string>
  <key>CFBundleIdentifier</key>      <string>com.sophie.timetracker.app</string>
  <key>CFBundleVersion</key>         <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleExecutable</key>      <string>TimeTracker</string>
  <key>CFBundleIconFile</key>        <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>  <string>11.0</string>
  <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/TimeTracker" <<'SH'
#!/bin/bash
URL="http://localhost:7799"
PLIST="$HOME/Library/LaunchAgents/com.sophie.timetracker-dashboard.plist"
if ! /usr/bin/curl -s -o /dev/null --max-time 2 "$URL"; then
  /bin/launchctl load "$PLIST" 2>/dev/null
  for i in $(seq 8); do /usr/bin/curl -s -o /dev/null --max-time 1 "$URL" && break; sleep 0.5; done
fi
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ -x "$CHROME" ]; then exec "$CHROME" --app="$URL" --window-size=1280,900
else exec /usr/bin/open "$URL"; fi
SH
chmod +x "$APP/Contents/MacOS/TimeTracker"

# 4. Register with Launch Services so Finder/Dock see it immediately.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
rm -f "$HERE/.icon.png"; rm -rf "$ICONSET"
echo "✓ Built $APP"
echo "  Drag it from /Applications onto your Dock to pin it."

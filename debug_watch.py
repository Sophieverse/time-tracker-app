#!/usr/bin/env python3
"""Live debug view of exactly what the tracker sees each tick.

Run this and use your computer normally for ~30s:

    python3 debug_watch.py

Each line shows: frontmost app | is it a tracked browser? | the tab URL it
would credit | idle seconds. Watch for a line where the frontmost app / URL
does NOT match what you're actually looking at — that's the bug, captured.
"""
import time
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tracker", os.path.join(HERE, "tracker.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

print(f"{'time':8} {'frontmost app':22} {'browser?':9} {'idle':6} url")
print("-" * 90)
try:
    while True:
        app = t.frontmost_app() or "(none)"
        label = t.BROWSER_APPS.get(app)
        url = t.active_tab_url(app) if label else ""
        dom = t.domain_of(url) or ""
        idle = round(t.idle_seconds(), 1)
        mark = label or "—"
        shown = dom if dom else (url[:50] if url else "")
        print(f"{time.strftime('%H:%M:%S'):8} {app[:22]:22} {mark:9} {idle:<6} {shown}")
        time.sleep(3)
except KeyboardInterrupt:
    print("\nstopped")

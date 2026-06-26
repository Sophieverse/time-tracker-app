#!/usr/bin/env python3
"""One-off: import the legacy daily-totals JSON logs (data/<date>.json from the
first version of the tracker) into the new SQLite event log.

The old format only stored per-domain second totals with no intra-day timing,
so we lay each domain out as a single sequential block starting at 9am local on
that date. The *totals, categories, and trends* are therefore exact; only the
timeline placement for these imported days is synthetic. Idempotent: skips any
date that already has events.
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime

import db

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.json$")


def main():
    conn = db.connect()
    imported = 0
    for path in sorted(glob.glob(os.path.join(db.DATA_DIR, "*.json"))):
        m = DATE_RE.search(path)
        if not m:
            continue
        date_str = m.group(1)
        try:
            domains = json.load(open(path)).get("domains", {})
        except Exception:
            continue
        if not domains:
            continue
        start, end = (datetime.strptime(date_str, "%Y-%m-%d").timestamp(),)*2
        # day already populated?
        day_start = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
        existing = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE ts >= ? AND ts < ?",
            (day_start, day_start + 86400)).fetchone()["c"]
        if existing:
            continue
        cursor = day_start + 9 * 3600  # 9am
        for domain, secs in sorted(domains.items(), key=lambda kv: -kv[1]):
            secs = float(secs)
            db.add_event(conn, ts=cursor, dur=secs, app="Browser (imported)",
                         is_browser=True, domain=domain, url=None, title=None)
            cursor += secs
            imported += 1
        print(f"  imported {len(domains)} domains for {date_str}")
    conn.close()
    print(f"done — {imported} domain-blocks imported")


if __name__ == "__main__":
    main()

"""Derives every dashboard view from the raw event log.

Nothing here is stored — it's all computed on demand from `events`, which is
what makes the granular model powerful: any time range, any grouping, any
rollup is just a query + a little Python. The three public functions map 1:1
to the server's API:

  day_summary(date)   → totals, focus score, by-category/domain/app, timeline
  range_summary(days) → per-day totals + focus + category split (for trends)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import db
import taxonomy

# Two adjacent events of the same (app, domain) within this gap are merged into
# one timeline block. Larger gaps start a new block (a "break").
SESSION_GAP = 120.0
# Timeline blocks shorter than this are dropped as noise (a stray flicker).
MIN_BLOCK = 8.0


def _day_bounds(date_str: str) -> tuple[float, float]:
    """[start, end) unix epoch for the given local YYYY-MM-DD."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = d.timestamp()
    end = (d + timedelta(days=1)).timestamp()
    return start, end


def _activity_key(row) -> tuple[str, str]:
    """(key, kind) used for categorization lookup."""
    if row["is_browser"] and row["domain"]:
        return row["domain"], "domain"
    return row["app"], "app"


def available_days(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date(ts,'unixepoch','localtime') AS d FROM events ORDER BY d DESC"
    ).fetchall()
    return [r["d"] for r in rows]


def _label(row, catmap) -> tuple[str, str, str]:
    """Return (display_key, category, color) for an event row."""
    key, _ = _activity_key(row)
    category = catmap.get(key, taxonomy.DEFAULT_CATEGORY)
    return key, category, taxonomy.color_of(category)


def day_summary(conn, date_str: str) -> dict:
    start, end = _day_bounds(date_str)
    rows = conn.execute(
        "SELECT * FROM events WHERE ts >= ? AND ts < ? ORDER BY ts",
        (start, end),
    ).fetchall()
    catmap = db.category_map(conn)

    by_cat: dict[str, float] = {}
    by_dom: dict[str, list] = {}   # domain → [seconds, category]
    by_app: dict[str, list] = {}
    total = 0.0
    focus = 0.0

    for r in rows:
        dur = r["dur"]
        total += dur
        key, cat, _ = _label(r, catmap)
        by_cat[cat] = by_cat.get(cat, 0.0) + dur
        if taxonomy.is_productive(cat):
            focus += dur
        if r["is_browser"] and r["domain"]:
            slot = by_dom.setdefault(r["domain"], [0.0, cat])
            slot[0] += dur
        slot = by_app.setdefault(r["app"], [0.0, cat])
        slot[0] += dur

    timeline = _build_timeline(rows, catmap)

    def cat_rows():
        out = []
        for name, secs in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            out.append({"category": name, "seconds": round(secs),
                        "color": taxonomy.color_of(name),
                        "productive": taxonomy.is_productive(name)})
        return out

    def dom_rows():
        out = []
        for dom, (secs, cat) in sorted(by_dom.items(), key=lambda kv: -kv[1][0]):
            out.append({"domain": dom, "seconds": round(secs),
                        "category": cat, "color": taxonomy.color_of(cat)})
        return out

    def app_rows():
        out = []
        for app, (secs, cat) in sorted(by_app.items(), key=lambda kv: -kv[1][0]):
            out.append({"app": app, "seconds": round(secs),
                        "category": cat, "color": taxonomy.color_of(cat)})
        return out

    return {
        "date": date_str,
        "total_seconds": round(total),
        "active_seconds": round(total),
        "focus_seconds": round(focus),
        "focus_score": round(focus / total * 100) if total else 0,
        "by_category": cat_rows(),
        "by_domain": dom_rows(),
        "by_app": app_rows(),
        "timeline": timeline,
    }


def _build_timeline(rows, catmap) -> list[dict]:
    """Merge consecutive same-activity events into blocks for the 24h strip."""
    blocks: list[dict] = []
    cur = None
    for r in rows:
        key, cat, color = _label(r, catmap)
        ev_start = r["ts"]
        ev_end = r["ts"] + r["dur"]
        same = cur and cur["_key"] == key
        contiguous = cur and ev_start - cur["end"] <= SESSION_GAP
        if same and contiguous:
            cur["end"] = ev_end
            if r["title"] and not cur["title"]:
                cur["title"] = r["title"]
        else:
            if cur:
                blocks.append(cur)
            cur = {
                "_key": key,
                "start": ev_start,
                "end": ev_end,
                "app": r["app"],
                "domain": r["domain"],
                "title": r["title"],
                "category": cat,
                "color": color,
            }
    if cur:
        blocks.append(cur)

    out = []
    for b in blocks:
        secs = b["end"] - b["start"]
        if secs < MIN_BLOCK:
            continue
        b.pop("_key", None)
        b["start"] = round(b["start"])
        b["end"] = round(b["end"])
        b["seconds"] = round(secs)
        out.append(b)
    return out


def range_summary(conn, days: int = 7) -> dict:
    """Per-day totals + focus + category split for the last `days` days
    (chronological, oldest first), for the weekly-trends chart."""
    today = datetime.now().date()
    catmap = db.category_map(conn)
    out_days = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        start, end = _day_bounds(date_str)
        rows = conn.execute(
            "SELECT app, is_browser, domain, dur FROM events WHERE ts >= ? AND ts < ?",
            (start, end),
        ).fetchall()
        by_cat: dict[str, float] = {}
        total = focus = 0.0
        for r in rows:
            key, _ = _activity_key(r)
            cat = catmap.get(key, taxonomy.DEFAULT_CATEGORY)
            total += r["dur"]
            by_cat[cat] = by_cat.get(cat, 0.0) + r["dur"]
            if taxonomy.is_productive(cat):
                focus += r["dur"]
        out_days.append({
            "date": date_str,
            "total_seconds": round(total),
            "focus_seconds": round(focus),
            "by_category": [
                {"category": n, "seconds": round(s), "color": taxonomy.color_of(n)}
                for n, s in sorted(by_cat.items(), key=lambda kv: -kv[1])
            ],
        })
    return {"days": out_days}

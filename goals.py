"""Daily focus goal + per-category time limits, with progress and streaks.

Goals live in goals.json (easy to hand-edit, settable from the dashboard via
POST /api/goals). Everything else is computed live from the event log:
  - today's focus minutes vs the daily focus goal,
  - minutes in each limited category vs its cap,
  - a streak of consecutive days that met the focus goal (an in-progress today
    that hasn't hit goal yet doesn't break the streak),
  - a 7-day met/not-met strip.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import db
import taxonomy

GOALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goals.json")

DEFAULTS = {
    "focus_daily_minutes": 240,
    "limits": [
        {"category": "Social Media", "max_minutes": 30},
        {"category": "Entertainment", "max_minutes": 45},
    ],
}


def load() -> dict:
    try:
        with open(GOALS_PATH) as f:
            g = json.load(f)
            g.setdefault("focus_daily_minutes", DEFAULTS["focus_daily_minutes"])
            g.setdefault("limits", [])
            return g
    except Exception:
        return dict(DEFAULTS)


def save(g: dict) -> dict:
    clean = {
        "focus_daily_minutes": int(g.get("focus_daily_minutes", 240)),
        "limits": [
            {"category": l["category"], "max_minutes": int(l["max_minutes"])}
            for l in g.get("limits", [])
            if l.get("category") in taxonomy.CATEGORIES and l.get("max_minutes")
        ],
    }
    with open(GOALS_PATH, "w") as f:
        json.dump(clean, f, indent=2)
    return clean


def _day_bounds(date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


def _day_focus_and_cats(conn, date_str: str):
    """(focus_minutes, total_minutes, {category: minutes}) for a local day."""
    start, end = _day_bounds(date_str)
    rows = conn.execute(
        "SELECT app, is_browser, domain, dur FROM events WHERE ts >= ? AND ts < ?",
        (start, end)).fetchall()
    catmap = db.category_map(conn)
    by_cat: dict[str, float] = {}
    focus = total = 0.0
    for r in rows:
        key = r["domain"] if (r["is_browser"] and r["domain"]) else r["app"]
        cat = catmap.get(key, taxonomy.DEFAULT_CATEGORY)
        total += r["dur"]
        by_cat[cat] = by_cat.get(cat, 0.0) + r["dur"]
        if taxonomy.is_productive(cat):
            focus += r["dur"]
    return (focus / 60.0, total / 60.0,
            {c: round(s / 60.0) for c, s in by_cat.items()})


def status(conn) -> dict:
    g = load()
    goal = g["focus_daily_minutes"]
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")

    focus_min, total_min, by_cat = _day_focus_and_cats(conn, today_str)
    focus_min = round(focus_min)

    # 7-day strip (oldest first)
    week = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        fm = round(_day_focus_and_cats(conn, d.strftime("%Y-%m-%d"))[0])
        week.append({"date": d.strftime("%Y-%m-%d"), "focus_minutes": fm,
                     "met": fm >= goal})

    # streak: consecutive met days ending today; an unmet in-progress today is
    # skipped (anchor at yesterday) rather than breaking the streak.
    def met(d):
        return round(_day_focus_and_cats(conn, d.strftime("%Y-%m-%d"))[0]) >= goal
    streak = 0
    cursor = today if met(today) else today - timedelta(days=1)
    while met(cursor):
        streak += 1
        cursor -= timedelta(days=1)
        if streak > 365:
            break

    return {
        "focus_daily_minutes": goal,
        "limits": g["limits"],
        "today": {
            "focus_minutes": focus_min,
            "total_minutes": round(total_min),
            "focus_score": round(focus_min / total_min * 100) if total_min else 0,
            "by_category_minutes": by_cat,
        },
        "streak_days": streak,
        "week": week,
    }

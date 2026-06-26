"""SQLite storage — the granular event log that everything else derives from.

Two tables:
  events     — one row per credited sample interval (the raw, fine-grained log).
               This is the source of truth; timelines, sessions, category
               breakdowns and trends are all just queries over it.
  categories — cache of the category assigned to each domain/app, so we never
               re-ask Claude (or re-run heuristics) for a key we've seen.

A tiny `meta` key/value table tracks sync bookkeeping.

WAL mode is on so the always-on tracker can write while the dashboard server
reads, with no lock contention.
"""
from __future__ import annotations

import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA_DIR, "tracker.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,           -- interval start, unix epoch (seconds)
    dur     REAL    NOT NULL,           -- seconds credited to this interval
    app     TEXT    NOT NULL,           -- frontmost app name
    is_browser INTEGER NOT NULL DEFAULT 0,
    domain  TEXT,                        -- bare host for browser activity, else NULL
    url     TEXT,
    title   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS categories (
    key      TEXT PRIMARY KEY,          -- domain (browser) or app name (native)
    kind     TEXT NOT NULL,             -- 'domain' | 'app'
    category TEXT NOT NULL,
    source   TEXT NOT NULL,             -- 'heuristic' | 'claude' | 'manual'
    updated  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def add_event(conn, ts: float, dur: float, app: str, is_browser: bool,
              domain: str | None, url: str | None, title: str | None) -> None:
    conn.execute(
        """INSERT INTO events (ts, dur, app, is_browser, domain, url, title)
           VALUES (?,?,?,?,?,?,?)""",
        (ts, dur, app, 1 if is_browser else 0, domain, url, title),
    )
    conn.commit()


# ── category cache ───────────────────────────────────────────────────────────

def get_category(conn, key: str) -> str | None:
    row = conn.execute("SELECT category FROM categories WHERE key=?", (key,)).fetchone()
    return row["category"] if row else None


def set_category(conn, key: str, kind: str, category: str, source: str) -> None:
    conn.execute(
        """INSERT INTO categories (key, kind, category, source, updated)
           VALUES (?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET
             category=excluded.category, source=excluded.source, updated=excluded.updated""",
        (key, kind, category, source, time.time()),
    )
    conn.commit()


def category_map(conn) -> dict[str, str]:
    """All known key → category, for fast lookup during aggregation."""
    return {r["key"]: r["category"]
            for r in conn.execute("SELECT key, category FROM categories")}


def uncategorized_keys(conn, since_days: int = 30) -> list[tuple[str, str]]:
    """Distinct (key, kind) seen in recent events that have no category yet.
    key is domain for browser events, app name for native ones."""
    cutoff = time.time() - since_days * 86400
    rows = conn.execute(
        """SELECT DISTINCT
               CASE WHEN is_browser=1 AND domain IS NOT NULL THEN domain ELSE app END AS key,
               CASE WHEN is_browser=1 AND domain IS NOT NULL THEN 'domain' ELSE 'app' END AS kind
           FROM events
           WHERE ts > ?""",
        (cutoff,),
    ).fetchall()
    known = set(category_map(conn))
    return [(r["key"], r["kind"]) for r in rows if r["key"] and r["key"] not in known]


# ── meta ─────────────────────────────────────────────────────────────────────

def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()

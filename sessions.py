"""Turns raw events into AI-labeled activity sessions — the "story of the day".

Two steps, run on a cadence by the tracker:

  rebuild() — recompute deterministic blocks from recent events (same
              sessionization analytics uses) and persist the *finalized* ones
              (those whose activity ended more than SESSION_GAP ago, so they
              won't grow further) into the sessions table.

  label()   — for each unlabeled, long-enough session, hand Claude the domains
              and the actual page/window TITLES the user saw, with dwell times,
              and ask what they were DOING ("Shopping for floor lamps"), a one
              line summary, and the distinct sub-tasks. Cached forever.

This is the same structured-output + prompt-caching approach the rize-clone uses
to name blocks, adapted to label sessions from titles.
"""
from __future__ import annotations

import json
import os
import time

import analytics
import db

LABEL_MIN = 60          # don't persist/label sessions shorter than this (noise)
LABEL_MIN_AGE = analytics.SESSION_GAP + 30  # a block is "finalized" once this old
BATCH = 10


def rebuild(conn, lookback_hours: int = 36) -> int:
    now = time.time()
    start = now - lookback_hours * 3600
    rows = conn.execute("SELECT * FROM events WHERE ts >= ? ORDER BY ts", (start,)).fetchall()
    catmap = db.category_map(conn)
    blocks = analytics._build_timeline(rows, catmap)
    n = 0
    for b in blocks:
        finalized = b["end"] < now - LABEL_MIN_AGE
        if finalized and b["seconds"] >= LABEL_MIN:
            db.upsert_session(conn, b["start"], b["end"], b["seconds"],
                              b["app"], b["domain"], b["category"])
            n += 1
    return n


def _resolve_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    cfg = os.path.join(os.path.expanduser("~"), ".claude", "email_config.json")
    try:
        with open(cfg) as f:
            return json.load(f).get("anthropic_api_key") or None
    except Exception:
        return None


_SYSTEM = (
    "You label blocks of activity for a personal time tracker (like Rize). For "
    "each block you get the app, the website domain, and the actual page/window "
    "TITLES the user saw, with how long each was on screen. Infer what the user "
    "was actually DOING and return, per block:\n"
    "  - title: a short, specific session name (4-8 words). Prefer the concrete "
    "thing ('Shopping for floor lamps', 'Reviewing recon PR on GitHub') over the "
    "generic ('Browsing Amazon').\n"
    "  - summary: one plain sentence describing the block.\n"
    "  - tasks: a list of the distinct things done in the block, each a short "
    "phrase (3-7 words); one entry if it was a single continuous task.\n"
    "Base the judgment on the TITLES, not just the domain — the same site hosts "
    "very different activities."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "tasks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "summary", "tasks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


def _brief(conn, s) -> str:
    start, end = s["start_ts"], s["end_ts"]
    domain, app = s["domain"], s["app"]
    if domain:
        rows = conn.execute(
            "SELECT title, SUM(dur) d FROM events WHERE ts >= ? AND ts <= ? AND domain=? "
            "GROUP BY title ORDER BY d DESC", (start, end, domain)).fetchall()
        where = f"{app} · {domain}"
    else:
        rows = conn.execute(
            "SELECT title, SUM(dur) d FROM events WHERE ts >= ? AND ts <= ? AND domain IS NULL "
            "AND app=? GROUP BY title ORDER BY d DESC", (start, end, app)).fetchall()
        where = app
    when = time.strftime("%a %b %d %I:%M%p", time.localtime(start))
    mins = round(s["seconds"] / 60)
    lines = [f"BLOCK id={int(start)} | {when} | ~{mins} min | {where} | {s['category']}"]
    for r in rows[:12]:
        if r["title"]:
            lines.append(f"  - ({round((r['d'] or 0)/60)}m) {r['title']}")
    if len(lines) == 1:
        lines.append("  - (no page titles captured)")
    return "\n".join(lines)


def _heuristic_fallback(conn, sess_list, report) -> None:
    """Label sessions from their page titles without an LLM. Keeps the tracker
    from getting stuck on "labeling" when the Claude API is unavailable."""
    import heuristic_labels as hl
    for s in sess_list:
        try:
            title, summary, tasks = hl.heuristic_label(conn, s)
            db.label_session(conn, s["start_ts"], title, summary, json.dumps(tasks))
            report["labeled"] += 1
        except Exception:  # noqa: BLE001
            pass


def label(conn) -> dict:
    todo = [s for s in db.unlabeled_sessions(conn) if s["seconds"] >= LABEL_MIN]
    report = {"labeled": 0, "error": None}
    if not todo:
        return report
    api_key = _resolve_api_key()
    # Without a working API key or SDK, fall back to heuristic labels so the
    # dashboard always shows *something* instead of a permanent "labeling…".
    if not api_key:
        report["error"] = "no API key — used heuristic labels"
        _heuristic_fallback(conn, todo, report)
        return report
    try:
        import anthropic
    except Exception:
        report["error"] = "anthropic SDK missing — used heuristic labels"
        _heuristic_fallback(conn, todo, report)
        return report

    client = anthropic.Anthropic(api_key=api_key)
    by_id = {int(s["start_ts"]): s for s in todo}
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        user = ("Label each block; echo its id.\n\n" +
                "\n\n".join(_brief(conn, s) for s in batch))
        try:
            resp = client.messages.create(
                model="claude-opus-4-8", max_tokens=3000,
                system=[{"type": "text", "text": _SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            text = next(b.text for b in resp.content if b.type == "text")
            labeled_ids = set()
            for lab in json.loads(text)["labels"]:
                s = by_id.get(lab["id"])
                if not s:
                    continue
                db.label_session(conn, s["start_ts"], lab["title"], lab["summary"],
                                 json.dumps(lab.get("tasks") or []))
                report["labeled"] += 1
                labeled_ids.add(lab["id"])
        except Exception as e:  # noqa: BLE001
            # API failed for this batch (auth/credit/network) → heuristic fallback
            report["error"] = str(e)[:200]
            _heuristic_fallback(conn, batch, report)
    return report


def run(conn=None) -> dict:
    own = conn is None
    conn = conn or db.connect()
    try:
        rebuild(conn)
        return label(conn)
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    print(run())

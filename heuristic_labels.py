"""Heuristic session labeler — derives a concrete title/summary/tasks for an
activity session from its actual page titles, WITHOUT calling an LLM.

Used as a fallback so sessions never get stuck on "labeling" when the Claude
API key is missing/invalid/out of credit. Labels are honest and title-driven:
they describe what the captured window titles show, nothing invented.
"""
import json
import re

# noise suffixes to strip from a page title to get the meaningful part
_SUFFIX_RE = re.compile(
    r"\s*[-–|]\s*(Gmail|Google Search|Google Docs|Google Sheets|Google Forms|"
    r"YouTube|Substack|Google Voice|Slack|Claude|Personal.*|Compose.*)\s*$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\s*[-–]\s*\S+@\S+.*$")          # drop "… - me@x.com - Gmail"
_SEARCH_RE = re.compile(r"^(.*?)\s*[-–]\s*Google Search\s*$", re.IGNORECASE)


def _clean(title: str) -> str:
    if not title:
        return ""
    t = title.strip()
    m = _SEARCH_RE.match(t)
    if m:                                   # "meloni and trump - Google Search"
        return f"Searched: {m.group(1).strip()}"
    t = _EMAIL_RE.sub("", t)
    t = _SUFFIX_RE.sub("", t)
    return t.strip(" -–|·")


def _top_titles(conn, s):
    """Distinct page titles in a session, longest-on-screen first."""
    start, end, domain, app = s["start_ts"], s["end_ts"], s["domain"], s["app"]
    if domain:
        q = ("SELECT title, SUM(dur) d FROM events WHERE ts>=? AND ts<=? AND domain=? "
             "GROUP BY title ORDER BY d DESC")
        rows = conn.execute(q, (start, end, domain)).fetchall()
    else:
        q = ("SELECT title, SUM(dur) d FROM events WHERE ts>=? AND ts<=? AND domain IS NULL "
             "AND app=? GROUP BY title ORDER BY d DESC")
        rows = conn.execute(q, (start, end, app)).fetchall()
    return [r["title"] for r in rows if r["title"]]


def heuristic_label(conn, s) -> tuple[str, str, list[str]]:
    """Return (title, summary, tasks) for one session row."""
    app, domain, cat = s["app"], s["domain"], s["category"]
    titles = _top_titles(conn, s)
    cleaned = []
    for t in titles:
        c = _clean(t)
        if c and c not in cleaned:
            cleaned.append(c)

    if cleaned:
        title = cleaned[0][:70]
        where = domain or app
        summary = f"{cat} on {where}: {cleaned[0]}." if where else f"{cleaned[0]}."
        tasks = cleaned[:3]
    else:
        # no page titles (e.g. Terminal, Finder, QuickTime) — honest app-level label
        title = f"{app} session"
        summary = f"Time in {app} ({cat}); no window titles captured."
        tasks = [f"{app} ({cat})"]
    return title, summary, tasks


def label_all_unlabeled(conn, min_seconds: int = 60) -> int:
    """Label every unlabeled session heuristically. Returns count labeled."""
    import db
    rows = conn.execute(
        "SELECT * FROM sessions WHERE labeled=0 AND seconds>=? ORDER BY start_ts DESC",
        (min_seconds,),
    ).fetchall()
    n = 0
    for s in rows:
        title, summary, tasks = heuristic_label(conn, s)
        db.label_session(conn, s["start_ts"], title, summary, json.dumps(tasks))
        n += 1
    return n

"""Assigns a category to every domain/app, in two cheap passes:

  1. Heuristics — a built-in map (taxonomy.DOMAIN_HEURISTICS / APP_HEURISTICS)
     covers the common cases instantly, with no API call. This means the
     dashboard is meaningfully categorized the moment data exists.

  2. Claude — anything the heuristics don't know is batched to Claude with a
     constrained (enum) JSON schema, then cached forever in the categories
     table. Uses the same API-key resolution as the daily-email pipeline.

Both passes write to the categories cache, so each key is resolved at most once.
If no API key is available, unknown keys simply stay Uncategorized until one is.
"""
from __future__ import annotations

import json

import db
import taxonomy

# Resolve the Anthropic key the same way the rize-clone / email pipeline does:
# env var first, then ~/.claude/email_config.json.
import os


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
    "You categorize websites and apps for a personal time tracker. "
    "For each item you are given a domain or app name (and sometimes example "
    "page titles). Assign the single best-fit category from the allowed list. "
    "Allowed categories: " + ", ".join(taxonomy.CATEGORIES) + ". "
    "Use 'Uncategorized' only when genuinely unclear. Judge by what the user is "
    "most likely DOING there (e.g. a bank site = Finance, a journal = Research)."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "category": {"type": "string", "enum": list(taxonomy.CATEGORIES)},
                },
                "required": ["key", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _titles_for(conn, key: str, kind: str, limit: int = 4) -> list[str]:
    col = "domain" if kind == "domain" else "app"
    rows = conn.execute(
        f"SELECT DISTINCT title FROM events WHERE {col}=? AND title IS NOT NULL "
        f"AND title != '' LIMIT ?",
        (key, limit),
    ).fetchall()
    return [r["title"] for r in rows]


def run(use_claude: bool = True) -> dict:
    """Categorize all currently-uncategorized keys. Returns a small report."""
    conn = db.connect()
    report = {"heuristic": 0, "claude": 0, "remaining": 0, "error": None}
    try:
        todo = db.uncategorized_keys(conn)
        if not todo:
            return report

        # Pass 1 — heuristics.
        still: list[tuple[str, str]] = []
        for key, kind in todo:
            cat = taxonomy.heuristic_category(key, kind)
            if cat:
                db.set_category(conn, key, kind, cat, "heuristic")
                report["heuristic"] += 1
            else:
                still.append((key, kind))

        if not still:
            return report

        # Pass 2 — Claude for the rest.
        if not use_claude:
            report["remaining"] = len(still)
            return report

        api_key = _resolve_api_key()
        if not api_key:
            report["error"] = "no API key; left as Uncategorized"
            report["remaining"] = len(still)
            return report

        try:
            import anthropic
        except Exception:
            report["error"] = "anthropic SDK not installed"
            report["remaining"] = len(still)
            return report

        client = anthropic.Anthropic(api_key=api_key)
        kind_of = dict(still)
        BATCH = 25
        for i in range(0, len(still), BATCH):
            batch = still[i:i + BATCH]
            lines = []
            for key, kind in batch:
                titles = "; ".join(_titles_for(conn, key, kind))
                lines.append(f"- {key} ({kind})" + (f" — e.g. {titles}" if titles else ""))
            user = "Categorize each item, echoing its key exactly:\n\n" + "\n".join(lines)
            try:
                resp = client.messages.create(
                    model="claude-opus-4-8",
                    max_tokens=2000,
                    system=[{"type": "text", "text": _SYSTEM,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": "low",
                                   "format": {"type": "json_schema", "schema": _SCHEMA}},
                )
                text = next(b.text for b in resp.content if b.type == "text")
                for item in json.loads(text)["items"]:
                    k = item["key"]
                    if k in kind_of:
                        db.set_category(conn, k, kind_of[k], item["category"], "claude")
                        report["claude"] += 1
            except Exception as e:  # noqa: BLE001
                report["error"] = str(e)[:200]
        return report
    finally:
        conn.close()


if __name__ == "__main__":
    print(run())

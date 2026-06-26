"""The category taxonomy: the buckets every domain/app is sorted into, each
with a display color and whether it counts as "productive" (focused) time.

The `productive` flag is what drives the focus score: focus_seconds is the sum
of time in productive categories, and focus_score = focus / active * 100.

Colors are chosen to be distinct on a dark background and are shared by the
backend (donut, bars, timeline) so the UI never has to invent its own.
"""
from __future__ import annotations

# name → (hex color, productive?)
CATEGORIES: dict[str, tuple[str, bool]] = {
    "Coding":        ("#4f9dff", True),
    "Research":      ("#7b6cff", True),
    "Writing":       ("#34c79a", True),
    "Design":        ("#ff8fb1", True),
    "Learning":      ("#5ec8d8", True),
    "AI Tools":      ("#a98bff", True),
    "Communication": ("#ffb454", True),
    "Finance":       ("#3fae6b", True),
    "Productivity":  ("#6ee7b7", True),
    "Social Media":  ("#ff6b8a", False),
    "Entertainment": ("#ff5c5c", False),
    "Shopping":      ("#c0a062", False),
    "News":          ("#9aa0a6", False),
    "Travel":        ("#f6c453", False),
    "Uncategorized": ("#6b7280", False),
}

DEFAULT_CATEGORY = "Uncategorized"


def color_of(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES[DEFAULT_CATEGORY])[0]


def is_productive(category: str) -> bool:
    return CATEGORIES.get(category, CATEGORIES[DEFAULT_CATEGORY])[1]


def taxonomy_list() -> list[dict]:
    return [
        {"name": name, "color": color, "productive": prod}
        for name, (color, prod) in CATEGORIES.items()
    ]


# A built-in heuristic map so categorization is useful immediately, even before
# (or entirely without) the Claude pass. Keys are matched as substrings of the
# domain (for browser activity) or the exact app name (for native apps).
DOMAIN_HEURISTICS: dict[str, str] = {
    # Coding
    "github.com": "Coding", "gitlab.com": "Coding", "stackoverflow.com": "Coding",
    "localhost": "Coding", "vercel.com": "Coding", "npmjs.com": "Coding",
    "developer.mozilla.org": "Coding", "readthedocs": "Coding",
    # AI Tools
    "claude.ai": "AI Tools", "chatgpt.com": "AI Tools", "openai.com": "AI Tools",
    "huggingface.co": "AI Tools", "anthropic.com": "AI Tools", "perplexity.ai": "AI Tools",
    "console.anthropic": "AI Tools",
    # Research / academic
    "arxiv.org": "Research", "academic.oup.com": "Research", "scholar.google": "Research",
    "jstor.org": "Research", "nature.com": "Research", "ssrn.com": "Research",
    "lesswrong.com": "Research", "alignmentforum.org": "Research",
    # Communication
    "mail.google.com": "Communication", "gmail.com": "Communication",
    "outlook": "Communication", "slack.com": "Communication", "discord.com": "Communication",
    "voice.google.com": "Communication", "messages": "Communication",
    # Finance
    "schwab.com": "Finance", "robinhood.com": "Finance", "tradingview.com": "Finance",
    "fidelity.com": "Finance", "coinbase.com": "Finance", "247wallst.com": "Finance",
    "bloomberg.com": "Finance", "marketwatch.com": "Finance",
    # Social
    "reddit.com": "Social Media", "twitter.com": "Social Media", "x.com": "Social Media",
    "instagram.com": "Social Media", "facebook.com": "Social Media",
    "linkedin.com": "Social Media", "tiktok.com": "Social Media",
    # Entertainment
    "youtube.com": "Entertainment", "netflix.com": "Entertainment",
    "twitch.tv": "Entertainment", "spotify.com": "Entertainment", "hulu.com": "Entertainment",
    # Shopping
    "amazon.com": "Shopping", "ebay.com": "Shopping", "etsy.com": "Shopping",
    # News
    "nytimes.com": "News", "wsj.com": "News", "theguardian.com": "News",
    "bbc.com": "News", "news.google.com": "News",
    # Travel
    "united.com": "Travel", "flyfrontier.com": "Travel", "alaskaair.com": "Travel",
    "booking.com": "Travel", "smartfares.com": "Travel", "expedia.com": "Travel",
    "airbnb.com": "Travel", "google.com/travel": "Travel", "kayak.com": "Travel",
    # Writing / notes
    "docs.google.com": "Writing", "notion.so": "Productivity", "obsidian": "Writing",
    # Productivity
    "calendar.google.com": "Productivity", "todoist.com": "Productivity",
    "linear.app": "Productivity", "asana.com": "Productivity",
}

# Native (non-browser) app name → category.
APP_HEURISTICS: dict[str, str] = {
    "Code": "Coding", "Visual Studio Code": "Coding", "Cursor": "Coding",
    "Terminal": "Coding", "iTerm2": "Coding", "Xcode": "Coding",
    "Obsidian": "Writing", "Notion": "Productivity", "Notes": "Writing",
    "Slack": "Communication", "Discord": "Communication", "Mail": "Communication",
    "Messages": "Communication", "zoom.us": "Communication", "FaceTime": "Communication",
    "Figma": "Design", "Sketch": "Design", "Photoshop": "Design",
    "Music": "Entertainment", "Spotify": "Entertainment", "TV": "Entertainment",
    "Toggl Track": "Productivity", "Things": "Productivity", "Fantastical": "Productivity",
    "Calendar": "Productivity", "Reminders": "Productivity",
    "Preview": "Research", "Books": "Learning",
}


def heuristic_category(key: str, kind: str) -> str | None:
    """Best-effort category from the built-in maps. Returns None if unknown."""
    if kind == "app":
        return APP_HEURISTICS.get(key)
    k = key.lower()
    for frag, cat in DOMAIN_HEURISTICS.items():
        if frag in k:
            return cat
    return None

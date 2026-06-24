"""News skill.

Top trending headlines from Google News' public RSS feeds — a keyless, stdlib-only HTTP
call (mirrors the weather skill). Three modes: overall top stories (no params), a named
topic section (world, business, technology, ...), or a free-text search.

The request host is a fixed literal and every user-supplied value is either mapped through
an allow-list (topic) or stripped to a code/keyword shape (query, locale), so a crafted
param can never alter the request's scheme or host — no SSRF, no file:// reads. Locale
defaults to en-US and can be overridden per call or via AETHER_NEWS_COUNTRY / AETHER_NEWS_LANG.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from ._util import fail, ok
from .registry import skill

# Google News section tokens, keyed by the friendly words a user (or messy STT) might say.
_TOPICS = {
    "world": "WORLD", "international": "WORLD", "global": "WORLD",
    "nation": "NATION", "national": "NATION", "us": "NATION", "local": "NATION",
    "business": "BUSINESS", "finance": "BUSINESS", "economy": "BUSINESS", "money": "BUSINESS",
    "technology": "TECHNOLOGY", "tech": "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT", "celebrity": "ENTERTAINMENT", "showbiz": "ENTERTAINMENT",
    "sport": "SPORTS", "sports": "SPORTS",
    "science": "SCIENCE",
    "health": "HEALTH",
}

# Keyword characters only. Stripping everything else means a crafted query can never alter
# the request's scheme or host (the host is a fixed literal below).
_SAFE_QUERY = re.compile(r"[^\w \-,.'&]", re.UNICODE)
_LANG = re.compile(r"[^a-z]")       # ISO 639-1, lowercased
_COUNTRY = re.compile(r"[^A-Z]")    # ISO 3166-1 alpha-2, uppercased


def _locale(params: dict) -> tuple[str, str]:
    """(lang, country) from params, then env, then en-US — each clamped to its code shape."""
    lang = str(params.get("lang") or os.environ.get("AETHER_NEWS_LANG") or "en")
    country = str(params.get("country") or os.environ.get("AETHER_NEWS_COUNTRY") or "US")
    lang = _LANG.sub("", lang.lower())[:2] or "en"
    country = _COUNTRY.sub("", country.upper())[:2] or "US"
    return lang, country


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; AetherAgent/1.0)"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosemgrep: fixed https host, sanitized path+params
        return resp.read()


def _items(xml_bytes: bytes, limit: int) -> list[dict]:
    """Parse up to `limit` headlines from a Google News RSS document."""
    root = ET.fromstring(xml_bytes)
    out: list[dict] = []
    for item in root.iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google News titles end with " - Publisher"; drop it since source is surfaced separately.
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        out.append({"title": title, "source": source,
                    "link": (item.findtext("link") or "").strip(),
                    "published": (item.findtext("pubDate") or "").strip()})
        if len(out) >= limit:
            break
    return out


@skill("news")
def news(params):
    """Top trending news headlines. params: {} (top stories) OR {"topic": "world|business|
    technology|science|health|sports|entertainment|nation"} OR {"query": "text"}; optional
    "limit" (1-10) and "country"/"lang" to override the locale (default en-US)."""
    try:
        limit = max(1, min(10, int(params.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    lang, country = _locale(params)
    suffix = f"hl={lang}-{country}&gl={country}&ceid={country}:{lang}"

    query = str(params.get("query") or "").strip()
    topic = str(params.get("topic") or "").strip().lower()

    mode, section_token, resolved_query = "top", None, None
    if query:
        safe = _SAFE_QUERY.sub("", query).strip()[:120]
        if not safe:
            return fail("Tell me what to search the news for.")
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(safe)}&{suffix}"
        heading, mode, resolved_query = f"Top results for “{safe}”", "search", safe
    elif topic:
        section = _TOPICS.get(topic)
        if not section:
            return fail(f"I don't have a “{topic}” news section. Try world, business, "
                        f"technology, science, health, sports, or entertainment.")
        url = f"https://news.google.com/rss/headlines/section/topic/{section}?{suffix}"
        heading, mode, section_token = f"Top {topic} news", "topic", section
    else:
        url = f"https://news.google.com/rss?{suffix}"
        heading = "Top stories right now"

    try:
        xml_bytes = _fetch(url)
    except Exception as e:  # noqa: BLE001
        return fail("I couldn't reach the news service just now.", error=str(e))

    try:
        items = _items(xml_bytes, limit)
    except ET.ParseError as e:
        return fail("The news service returned something I couldn't read.", error=str(e))
    if not items:
        return fail("The news service returned no headlines.")

    parts = [f"{it['title']} ({it['source']})" if it["source"] else it["title"] for it in items]
    summary = f"{heading}: " + "; ".join(parts) + "."
    return ok(summary, items=items, count=len(items), mode=mode,
              topic=section_token, query=resolved_query, source="google-news")

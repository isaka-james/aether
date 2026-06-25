"""Web search skill.

Keyless, stdlib-only web search via DuckDuckGo's HTML endpoint — the general "look it up
online" fallback for current facts and questions the dedicated news/weather skills don't
cover. Mirrors their safety model: the request host is a fixed literal and the query is
stripped to a keyword shape, so a crafted param can never change the request's scheme or
host (no SSRF, no file:// reads). Result titles and snippets are returned for the agent to
distil into a short spoken answer; nothing is fetched beyond the search page and nothing runs.
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from ._util import fail, ok
from .registry import skill

_ENDPOINT = "https://html.duckduckgo.com/html/"
# Keyword characters only — stripping the rest means a crafted query can't alter the request's
# scheme or host (the host is the fixed literal above; the query travels in the POST body).
_SAFE_QUERY = re.compile(r"[^\w \-,.'&?]", re.UNICODE)


def _real_url(href: str) -> str:
    """DuckDuckGo wraps result links as /l/?uddg=<encoded>; unwrap to the real URL when present."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urllib.parse.urlparse(href)
        if parsed.path.endswith("/l/"):
            uddg = urllib.parse.parse_qs(parsed.query).get("uddg")
            if uddg:
                return uddg[0]
    except ValueError:
        pass
    return href


class _ResultParser(HTMLParser):
    """Pull (title, snippet, url) triples out of DuckDuckGo's HTML results page. Defensive:
    tolerates a result with no snippet, ignores everything outside result rows, and stops
    collecting once `limit` results are in hand."""

    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._grab: str | None = None      # "title" | "snippet" | None
        self._buf: list[str] = []

    def _flush_pending(self) -> None:
        # A result whose snippet never arrived still counts — keep it rather than drop it.
        if self._cur is not None and self._cur.get("title"):
            self.results.append(self._cur)
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if len(self.results) >= self.limit:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._flush_pending()
            self._cur = {"title": "", "snippet": "", "url": _real_url(a.get("href", ""))}
            self._grab, self._buf = "title", []
        elif "result__snippet" in cls and self._cur is not None:
            self._grab, self._buf = "snippet", []

    def handle_data(self, data):
        if self._grab:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._cur is None or not self._grab:
            return
        if self._grab == "title" and tag == "a":
            self._cur["title"] = html.unescape("".join(self._buf)).strip()
            self._grab = None
        elif self._grab == "snippet":
            self._cur["snippet"] = html.unescape("".join(self._buf)).strip()
            self.results.append(self._cur)
            self._cur, self._grab = None, None

    def close(self):
        super().close()
        self._flush_pending()


def _fetch(query: str) -> bytes:
    body = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(
        _ENDPOINT, data=body,  # POST keeps the query out of the (fixed) URL entirely
        headers={"User-Agent": "Mozilla/5.0 (compatible; AetherAgent/1.0)"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosemgrep: fixed https host, sanitized body
        return resp.read()


@skill("web_search")
def web_search(params):
    """Search the web (keyless, via DuckDuckGo) and return the top results' titles and snippets
    for the agent to summarise. params: {"query": "text"} (+ optional "limit" 1-8). The general
    fallback for current facts and look-ups not covered by the news/weather skills."""
    query = str(params.get("query") or params.get("q") or "").strip()
    safe = _SAFE_QUERY.sub("", query).strip()[:200]
    if not safe:
        return fail("Tell me what to search the web for.")
    try:
        limit = max(1, min(8, int(params.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5

    try:
        body = _fetch(safe)
    except Exception as e:  # noqa: BLE001
        return fail("I couldn't reach the search service just now.", error=str(e))

    parser = _ResultParser(limit)
    try:
        parser.feed(body.decode("utf-8", "replace"))
        parser.close()
    except Exception as e:  # noqa: BLE001 - malformed markup shouldn't crash the agent
        return fail("The search service returned something I couldn't read.", error=str(e))

    results = parser.results[:limit]
    if not results:
        return fail(f"I found nothing for “{safe}”.")

    parts = [f"{r['title']}: {r['snippet']}" if r["snippet"] else r["title"] for r in results]
    summary = f"Top web results for “{safe}”: " + " | ".join(parts)
    return ok(summary, results=results, count=len(results), query=safe, source="duckduckgo")

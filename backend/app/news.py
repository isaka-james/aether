"""Integration with N.E.W.S. (the user's personal news-briefing service).

Aether logs into the n.e.w.s API with the configured account and fetches today's
cached briefing, then condenses it (title + top headlines per compass layer) so the
agent can read it back. n.e.w.s runs in its own Docker stack on the host (nginx :4291);
the backend reaches it via the docker host-gateway.
"""
import logging

import httpx

from . import cache
from .config import get_settings

log = logging.getLogger("aether.news")
_LAYERS = ("N", "E", "W", "S")  # City / Country / Continent / World


async def get_briefing() -> dict:
    s = get_settings()
    if not (s.news_email and s.news_password):
        return {"ok": False, "summary": "News isn't set up yet — add AETHER_NEWS_EMAIL and "
                "AETHER_NEWS_PASSWORD to the .env file.", "data": {}}
    cached = await cache.cache_get("news_briefing")
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            login = await c.post(f"{s.news_url}/auth/login",
                                 json={"email": s.news_email, "password": s.news_password})
            login.raise_for_status()
            token = login.json()["access_token"]
            r = await c.get(f"{s.news_url}/reports/today",
                            headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            report = r.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"ok": False, "summary": "My news login was rejected — check the credentials.", "data": {}}
        return {"ok": False, "summary": "Couldn't fetch the news briefing.", "data": {"error": str(e)}}
    except Exception as e:  # noqa: BLE001
        log.warning("news fetch failed: %s", e)
        return {"ok": False, "summary": "I couldn't reach your news service.", "data": {"error": str(e)}}

    if not report:
        return {"ok": True, "summary": "No news briefing has been generated for today yet — "
                "you can create one in the N.E.W.S. app.", "data": {"report": None}}

    sections = report.get("sections") or {}
    layers = []
    for key in _LAYERS:
        sec = sections.get(key) or {}
        heads = [st.get("headline", "").strip() for st in (sec.get("stories") or []) if st.get("headline")]
        if heads:
            layers.append({"label": sec.get("label", key), "headlines": heads[:3]})

    parts = [report.get("report_title") or "Today's briefing"]
    if report.get("opening_line"):
        parts.append(report["opening_line"])
    for l in layers:
        parts.append(f"{l['label']} — " + "; ".join(l["headlines"]))
    summary = ". ".join(parts)
    result = {"ok": True, "summary": summary,
              "data": {"title": report.get("report_title"), "date": report.get("report_date"),
                       "opening": report.get("opening_line"), "layers": layers}}
    await cache.cache_set("news_briefing", result, s.news_cache_ttl)
    return result

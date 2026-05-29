"""Integration with N.E.W.S. (the user's personal news-briefing service).

Aether logs into the n.e.w.s API with the configured account and fetches today's
cached briefing, then condenses it (a mood line + top headlines per compass layer) so the
agent can read it back. n.e.w.s runs in its own Docker stack on the host (nginx :4291);
the backend reaches it via the docker host-gateway.

Every return is a dict: {"ok": bool, "summary": <plain-language result>, "data": {...}}.
The summary is never empty and always says something the agent can voice — including on
every failure path — so a news request never ends in silence.
"""
import logging

import httpx

from . import cache
from .config import get_settings

log = logging.getLogger("aether.news")

# The compass layers, widening outward, with the spoken label each maps to. n.e.w.s keys
# its sections N/E/W/S (City / Country / Continent / World); we voice them, never the letter.
_LAYERS = (
    ("N", "Close to home"),
    ("E", "Around the country"),
    ("W", "Across the continent"),
    ("S", "Around the world"),
)


def _err(summary: str, **data) -> dict:
    return {"ok": False, "summary": summary, "data": data}


async def get_briefing() -> dict:
    s = get_settings()
    if not (s.news_email and s.news_password):
        return _err("The news service isn't set up yet — add AETHER_NEWS_EMAIL and "
                    "AETHER_NEWS_PASSWORD to the .env file to enable briefings.")

    cached = await cache.cache_get("news_briefing")
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            login = await c.post(f"{s.news_url}/auth/login",
                                 json={"email": s.news_email, "password": s.news_password})
            login.raise_for_status()
            token = (login.json() or {}).get("access_token")
            if not token:
                return _err("My news login came back without a token — the service may be "
                            "misbehaving.")
            r = await c.get(f"{s.news_url}/reports/today",
                            headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            report = r.json()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 401:
            return _err("My news login was rejected — the saved credentials look wrong.")
        if code >= 500:
            return _err("Your news service is having trouble of its own — it returned an error.",
                        error=f"{code}")
        return _err("I couldn't fetch today's news briefing.", error=f"{code}")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _err("I couldn't reach your news service — it may be offline.")
    except httpx.TimeoutException:
        return _err("Your news service took too long to answer, so I stopped waiting.")
    except Exception as e:  # noqa: BLE001
        log.warning("news fetch failed: %s", e)
        return _err("Something went wrong reaching your news service.", error=str(e))

    result = _shape(report)
    # Cache only a good, populated briefing so a transient empty result isn't pinned for minutes.
    if result["ok"] and result["data"].get("layers"):
        try:
            await cache.cache_set("news_briefing", result, s.news_cache_ttl)
        except Exception as e:  # noqa: BLE001
            log.warning("news cache_set failed: %s", e)
    return result


def _shape(report) -> dict:
    """Turn a /reports/today payload into a readable briefing. Tolerates a missing or
    malformed report rather than raising — the agent should always have something to say."""
    if not isinstance(report, dict):
        # `report` is None when no briefing has been generated for today.
        return {"ok": True, "data": {"report": None},
                "summary": "No news briefing has been generated for today yet — you can create "
                           "one in the N.E.W.S. app."}

    title = (report.get("report_title") or "").strip() or "Today's briefing"
    opening = (report.get("opening_line") or "").strip()
    closing = (report.get("closing_line") or "").strip()
    sections = report.get("sections")
    if not isinstance(sections, dict):
        sections = {}

    layers = []
    for key, label in _LAYERS:
        sec = sections.get(key)
        if not isinstance(sec, dict):
            continue
        stories = sec.get("stories")
        heads = []
        if isinstance(stories, list):
            for st in stories:
                if isinstance(st, dict) and (h := (st.get("headline") or "").strip()):
                    heads.append(h)
        mood = (sec.get("mood_line") or "").strip()
        if heads or mood:
            layers.append({"label": label, "mood": mood, "headlines": heads[:3]})

    if not layers:
        # A report exists but carries no usable stories — say so plainly, don't read silence.
        return {"ok": True, "data": {"title": title, "opening": opening, "layers": []},
                "summary": f"{title}. {opening} Today's briefing came through empty — no stories "
                           "made the cut across any of your layers.".strip()}

    # Shape the spoken briefing as discrete sentences (no em-dashes, no semicolons): each
    # layer gets its own sentence, with headlines joined by ", and " so the TTS chunker has
    # clean breaks and the result reads naturally aloud, never as one breathless run-on.
    parts = [title]
    if opening:
        parts.append(opening)
    for l in layers:
        if l["headlines"]:
            heads = l["headlines"]
            if len(heads) == 1:
                line = f"{l['label']}: {heads[0]}"
            else:
                line = f"{l['label']}: {', '.join(heads[:-1])}, and {heads[-1]}"
        elif l["mood"]:
            line = f"{l['label']}: {l['mood']}"
        else:
            continue
        parts.append(line)
    if closing:
        parts.append(closing)
    summary = ". ".join(p.rstrip(". ") for p in parts if p) + "."

    return {"ok": True, "summary": summary,
            "data": {"title": title, "date": report.get("report_date"),
                     "opening": opening, "closing": closing, "layers": layers}}

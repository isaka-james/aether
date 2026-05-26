"""Weather skill.

Leverages the location the user already configured in their KDE Plasma weather widget
(``placeDisplayName`` in the desktop appletsrc), then fetches live conditions from wttr.in
— a keyless, stdlib-only HTTP call. Falls back to IP-based geolocation if no KDE location
is set, and to an explicit ``location`` param when the user names a place.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from ._util import fail, ok
from .registry import skill

_APPLETSRC = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")


def _kde_location() -> str | None:
    """Read the place the user configured in the KDE weather widget, e.g. 'Dodoma, Tanzania'."""
    try:
        with open(_APPLETSRC) as f:
            for line in f:
                if line.startswith("placeDisplayName="):
                    place = line.split("=", 1)[1].strip()
                    # Drop a trailing ISO country code ("Dodoma, Tanzania, TZ" -> "Dodoma, Tanzania").
                    parts = [p.strip() for p in place.split(",") if p.strip()]
                    if len(parts) > 1 and len(parts[-1]) == 2 and parts[-1].isupper():
                        parts.pop()
                    if parts:
                        return ", ".join(parts)
    except OSError:
        pass
    return None


def _fetch(location: str | None) -> dict | None:
    """Fetch current weather as JSON from wttr.in. Returns the parsed dict, or None."""
    # wttr.in geolocates by IP when the path is empty; otherwise it resolves the place name.
    path = urllib.parse.quote(location) if location else ""
    url = f"https://wttr.in/{path}?format=j1"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})  # j1 JSON, not ANSI
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


@skill("weather")
def weather(params):
    """Current conditions and today's outlook for the user's location (KDE-configured) or a
    named place. params: {} or {"location": "Nairobi"}."""
    location = str(params.get("location") or "").strip() or _kde_location()
    try:
        data = _fetch(location)
    except Exception as e:  # noqa: BLE001
        return fail("I couldn't reach the weather service just now.", error=str(e))
    if not data:
        return fail("The weather service returned nothing usable.")

    cur = (data.get("current_condition") or [{}])[0]
    area = ((data.get("nearest_area") or [{}])[0])
    place = location or " ".join(
        v.get("value", "") for k in ("areaName", "country") for v in (area.get(k) or [{}]))
    place = place.strip() or "your area"

    temp = cur.get("temp_C")
    feels = cur.get("FeelsLikeC")
    desc = (cur.get("weatherDesc") or [{}])[0].get("value", "").strip()
    humidity = cur.get("humidity")
    wind = cur.get("windspeedKmph")

    today = (data.get("weather") or [{}])[0]
    tmax, tmin = today.get("maxtempC"), today.get("mintempC")

    bits = []
    if desc and temp:
        bits.append(f"{desc.lower()}, {temp}°C")
    elif temp:
        bits.append(f"{temp}°C")
    if feels and feels != temp:
        bits.append(f"feels like {feels}°C")
    if tmax and tmin:
        bits.append(f"high {tmax}° / low {tmin}°")
    if wind:
        bits.append(f"wind {wind} km/h")
    if humidity:
        bits.append(f"humidity {humidity}%")
    summary = f"In {place}: " + ", ".join(bits) + "." if bits else f"Weather for {place} is unavailable."

    source = "named" if params.get("location") else ("kde-widget" if location else "ip-geolocation")
    return ok(summary, location=place, temp_c=temp, feels_like_c=feels, condition=desc,
              high_c=tmax, low_c=tmin, humidity=humidity, wind_kmph=wind, source=source)

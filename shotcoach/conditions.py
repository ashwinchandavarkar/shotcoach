"""Conditions Scout: geocoding, weather, sun and moon for a place and date.

Uses Open-Meteo (free, no API key) and the astral library (computed locally),
so the app needs no extra secrets. No language model is involved here.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import requests
from astral import LocationInfo, moon
from astral.sun import SunDirection, blue_hour, golden_hour, sun

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 8


def geocode(place: str) -> dict | None:
    # Open-Meteo matches place names only, so "Acadia National Park, Maine"
    # is retried as "Acadia National Park" and then "Acadia".
    attempts = [place.strip()]
    if "," in place:
        attempts.append(place.split(",")[0].strip())
    words = attempts[-1].split()
    if len(words) > 1:
        attempts.append(words[0])
    for name in attempts:
        resp = requests.get(GEOCODE_URL, params={"name": name, "count": 1}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if results:
            r = results[0]
            label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
            return {
                "name": label,
                "latitude": r["latitude"],
                "longitude": r["longitude"],
                "timezone": r.get("timezone") or "UTC",
            }
    return None


def _fmt(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def _moon_phase_name(age: float) -> str:
    names = [
        (1.0, "New moon"), (6.4, "Waxing crescent"), (8.4, "First quarter"),
        (13.8, "Waxing gibbous"), (15.8, "Full moon"), (21.1, "Waning gibbous"),
        (23.1, "Last quarter"), (27.7, "Waning crescent"), (28.1, "New moon"),
    ]
    for limit, name in names:
        if age < limit:
            return name
    return "New moon"


def sun_and_moon(lat: float, lon: float, tz_name: str, date: dt.date) -> dict:
    tz = ZoneInfo(tz_name)
    loc = LocationInfo(latitude=lat, longitude=lon, timezone=tz_name)
    obs = loc.observer
    s = sun(obs, date=date, tzinfo=tz)
    out = {
        "sunrise": _fmt(s["sunrise"]),
        "sunset": _fmt(s["sunset"]),
    }
    try:
        gm = golden_hour(obs, date, SunDirection.RISING, tzinfo=tz)
        ge = golden_hour(obs, date, SunDirection.SETTING, tzinfo=tz)
        be = blue_hour(obs, date, SunDirection.SETTING, tzinfo=tz)
        out["golden_hour_morning"] = f"{_fmt(gm[0])} - {_fmt(gm[1])}"
        out["golden_hour_evening"] = f"{_fmt(ge[0])} - {_fmt(ge[1])}"
        out["blue_hour_evening"] = f"{_fmt(be[0])} - {_fmt(be[1])}"
    except ValueError:
        pass
    try:
        astro_dark = sun(obs, date=date, tzinfo=tz, dawn_dusk_depression=18)["dusk"]
        out["astronomical_dark_from"] = _fmt(astro_dark)
    except ValueError:
        out["astronomical_dark_from"] = "Not fully dark tonight (high latitude / season)"

    age = moon.phase(date)
    illum = round((1 - abs(age - 14.77) / 14.77) * 100)
    out["moon_phase"] = _moon_phase_name(age)
    out["moon_illumination_pct"] = max(0, min(100, illum))
    for key, fn in (("moonrise", moon.moonrise), ("moonset", moon.moonset)):
        try:
            t = fn(obs, date, tzinfo=tz)
            out[key] = _fmt(t) if t else "none"
        except ValueError:
            out[key] = "none"
    return out


def weather(lat: float, lon: float, date: dt.date) -> dict | None:
    """Forecast for the evening of `date` (Open-Meteo covers ~16 days ahead)."""

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloud_cover,precipitation_probability,wind_speed_10m,visibility,temperature_2m",
        "start_date": date.isoformat(),
        "end_date": date.isoformat(),
        "timezone": "auto",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    h = resp.json().get("hourly") or {}
    times = h.get("time") or []
    if not times:
        return None

    def at(hour: int) -> dict:
        i = min(hour, len(times) - 1)
        return {
            "cloud_cover_pct": h["cloud_cover"][i],
            "rain_chance_pct": h["precipitation_probability"][i],
            "wind_kmh": h["wind_speed_10m"][i],
            "temp_c": h["temperature_2m"][i],
        }

    return {"morning_7am": at(7), "midday_1pm": at(13), "evening_6pm": at(18), "night_10pm": at(22)}


def scout(place: str, date: dt.date | None = None) -> dict:
    """Everything the Settings Engineer needs to know about light and weather."""

    date = date or dt.date.today()
    geo = geocode(place)
    if not geo:
        return {"error": f"Could not find a place called '{place}'."}
    report = {"place": geo["name"], "date": date.isoformat(), "timezone": geo["timezone"]}
    report.update(sun_and_moon(geo["latitude"], geo["longitude"], geo["timezone"], date))
    days_ahead = (date - dt.date.today()).days
    if -1 <= days_ahead <= 15:
        report["weather"] = weather(geo["latitude"], geo["longitude"], date) or "unavailable"
    else:
        report["weather"] = "Forecast only available up to 16 days ahead."
    return report

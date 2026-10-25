"""Deterministic photography math the agents call as tools.

Language models are unreliable at arithmetic, so every number that can be
computed (shutter limits, depth of field, exposure) comes from here.
All focal lengths are the real focal length printed on the lens.
"""

from __future__ import annotations

import math

SHUTTER_STOPS = [
    30, 25, 20, 15, 13, 10, 8, 6, 5, 4, 3.2, 2.5, 2, 1.6, 1.3, 1, 0.8, 0.6, 0.5, 0.4, 0.3,
    1 / 4, 1 / 5, 1 / 6, 1 / 8, 1 / 10, 1 / 13, 1 / 15, 1 / 20, 1 / 25, 1 / 30, 1 / 40,
    1 / 50, 1 / 60, 1 / 80, 1 / 100, 1 / 125, 1 / 160, 1 / 200, 1 / 250, 1 / 320, 1 / 400,
    1 / 500, 1 / 640, 1 / 800, 1 / 1000, 1 / 1250, 1 / 1600, 1 / 2000, 1 / 2500, 1 / 3200,
    1 / 4000, 1 / 5000, 1 / 6400, 1 / 8000, 1 / 16000,
]
ISO_STOPS = [
    50, 64, 80, 100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000,
    2500, 3200, 4000, 5000, 6400, 8000, 10000, 12800, 16000, 20000, 25600, 32000, 51200,
    102400,
]
APERTURE_STOPS = [
    1.2, 1.4, 1.6, 1.7, 1.8, 2, 2.2, 2.5, 2.8, 3.2, 3.5, 4, 4.5, 5, 5.6, 6.3, 7.1, 8, 9, 10,
    11, 13, 14, 16, 18, 20, 22, 32,
]

# Typical scene brightness as EV at ISO 100. Sources: standard exposure-value
# tables (e.g. the Sunny 16 rule and published EV charts), rounded.
SCENE_EV100 = {
    "snow_or_beach_bright_sun": 16,
    "bright_sun": 15,
    "hazy_sun": 14,
    "cloudy_bright": 13,
    "golden_hour": 12,
    "overcast_or_open_shade": 11,
    "sunset_sky": 12,
    "heavy_overcast_or_forest_shade": 10,
    "blue_hour": 8,
    "indoor_bright": 8,
    "night_stadium_or_floodlit_sports": 7,
    "brightly_lit_city_street_night": 6,
    "indoor_dim_or_gym": 6,
    "city_skyline_night": 4,
    "full_moon_landscape": -3,
    "aurora": -4,
    "milky_way_dark_sky": -6.5,
}


def format_shutter(seconds: float) -> str:
    if seconds >= 0.3:
        return f"{seconds:g}s"
    return f"1/{round(1 / seconds)}s"


def parse_shutter(text: str | float) -> float:
    """Accept '1/250', '1/250s', '2s', '0.5' or a number and return seconds."""

    if isinstance(text, (int, float)):
        return float(text)
    value = str(text).strip().lower().rstrip("s").replace('"', "").strip()
    if "/" in value:
        num, den = value.split("/", 1)
        return float(num) / float(den)
    return float(value)


def _nearest(value: float, stops: list[float], log: bool = True) -> float:
    key = (lambda s: abs(math.log(s) - math.log(value))) if log else (lambda s: abs(s - value))
    return min(stops, key=key)


def snap_shutter(seconds: float, prefer: str = "nearest") -> float:
    """Snap to a real 1/3-stop shutter value. prefer='faster' never rounds slower."""

    if prefer == "faster":
        faster = [s for s in SHUTTER_STOPS if s <= seconds * 1.0001]
        return max(faster) if faster else min(SHUTTER_STOPS)
    if prefer == "slower_or_equal_limit":
        # For "max shutter" limits: largest standard value not exceeding the limit.
        ok = [s for s in SHUTTER_STOPS if s <= seconds * 1.0001]
        return max(ok) if ok else min(SHUTTER_STOPS)
    return _nearest(seconds, SHUTTER_STOPS)


def snap_iso(iso: float) -> int:
    return int(_nearest(max(iso, 50), ISO_STOPS))


def snap_aperture(n: float) -> float:
    return _nearest(n, APERTURE_STOPS)


def circle_of_confusion_mm(crop: float) -> float:
    return round(0.030 / crop, 4)


def depth_of_field(focal_mm: float, aperture: float, subject_distance_m: float, crop: float) -> dict:
    """Near/far limits of acceptable sharpness (thin-lens hyperfocal formulas)."""

    c = circle_of_confusion_mm(crop)
    f = focal_mm
    s = subject_distance_m * 1000
    hyper = f * f / (aperture * c) + f
    near = s * (hyper - f) / (hyper + s - 2 * f)
    far = math.inf if s >= hyper else s * (hyper - f) / (hyper - s)
    return {
        "circle_of_confusion_mm": c,
        "hyperfocal_m": round(hyper / 1000, 2),
        "near_limit_m": round(near / 1000, 2),
        "far_limit_m": "infinity" if far == math.inf else round(far / 1000, 2),
        "total_dof_m": "infinite" if far == math.inf else round((far - near) / 1000, 2),
    }


def hyperfocal(focal_mm: float, aperture: float, crop: float) -> dict:
    c = circle_of_confusion_mm(crop)
    h = (focal_mm * focal_mm / (aperture * c) + focal_mm) / 1000
    return {
        "hyperfocal_m": round(h, 2),
        "sharp_from_m": round(h / 2, 2),
        "advice": f"Focus at about {h:.1f} m; everything from {h / 2:.1f} m to infinity is acceptably sharp.",
    }


def star_shutter_limit(focal_mm: float, aperture: float, crop: float, pixel_pitch_um: float) -> dict:
    """Longest exposure before stars trail: classic 500 rule and the stricter NPF rule."""

    rule_500 = 500 / (focal_mm * crop)
    npf = (35 * aperture + 30 * pixel_pitch_um) / focal_mm
    rec = snap_shutter(npf, prefer="slower_or_equal_limit")
    return {
        "rule_500_s": round(rule_500, 1),
        "npf_rule_s": round(npf, 1),
        "recommended_max_shutter": format_shutter(rec),
        "note": "NPF is stricter and suits modern high-resolution sensors; use it for pin-sharp stars.",
    }


def motion_freeze_shutter(
    subject_speed_kmh: float,
    subject_distance_m: float,
    focal_mm: float,
    pixel_pitch_um: float,
    crop: float,
    direction: str = "across",
) -> dict:
    """Slowest shutter that keeps motion blur invisible at normal screen viewing.

    Blur budget is 2x the circle of confusion: sharp on a phone or laptop
    screen, slightly soft only when pixel-peeping at 100%.
    """

    v = subject_speed_kmh / 3.6
    factor = {"across": 1.0, "diagonal": 0.7, "toward": 0.3}.get(direction, 1.0)
    image_speed_mm_s = v * factor * focal_mm / subject_distance_m  # f and d both scale to mm/m
    blur_limit_mm = 2 * circle_of_confusion_mm(crop)
    t = blur_limit_mm / image_speed_mm_s if image_speed_mm_s > 0 else 1 / 60
    rec = snap_shutter(t, prefer="faster")
    blur_px = image_speed_mm_s * rec / (pixel_pitch_um / 1000)
    return {
        "slowest_freeze_shutter": format_shutter(rec),
        "motion_blur_pixels_at_that_speed": round(blur_px, 1),
        "assumption": f"Subject moving {direction} the frame at {subject_speed_kmh:g} km/h, {subject_distance_m:g} m away.",
    }


def handheld_min_shutter(focal_mm: float, crop: float, stabilized: bool) -> dict:
    """Reciprocal rule, crediting stabilization with a conservative 3 stops."""

    base = 1 / (focal_mm * crop)
    stops = 3 if stabilized else 0
    limit = base * (2 ** stops)
    rec = snap_shutter(limit, prefer="faster")
    return {
        "slowest_handheld_shutter": format_shutter(rec),
        "stabilization_stops_assumed": stops,
        "note": "For people in the shot, also keep at least 1/125s so they don't blur.",
    }


def solve_exposure(
    scene_light: str,
    aperture: float | None = None,
    shutter: str | float | None = None,
    iso: float | None = None,
    exposure_compensation: float = 0.0,
) -> dict:
    """Given two of aperture/shutter/ISO and the scene type, solve the third."""

    if scene_light not in SCENE_EV100:
        return {"error": f"Unknown scene_light. Choose one of: {', '.join(SCENE_EV100)}"}
    ev = SCENE_EV100[scene_light] - exposure_compensation
    t = parse_shutter(shutter) if shutter not in (None, "") else None
    given = sum(x is not None for x in (aperture, t, iso))
    if given < 2:
        return {"error": "Provide two of aperture, shutter, iso."}

    if t is None:
        t = aperture**2 / (2 ** (ev + math.log2(iso / 100)))
        t = snap_shutter(t)
    elif iso is None:
        iso = snap_iso(100 * aperture**2 / (t * 2**ev))
    elif aperture is None:
        aperture = snap_aperture(math.sqrt(t * 2**ev * iso / 100))

    return {
        "scene_ev100": SCENE_EV100[scene_light],
        "aperture": f"f/{aperture:g}",
        "shutter": format_shutter(t),
        "iso": int(iso),
        "note": "Starting point; check the histogram and adjust exposure compensation.",
    }

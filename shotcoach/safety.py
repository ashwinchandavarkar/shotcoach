"""Safety layer: input guards, physical-risk advisories, and output validation.

Every block or correction is written to logs/safety.jsonl so the defenses can
be shown working (report evidence), not just described.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import calculators as calc
from .config import LOGS_DIR

MAX_PROMPT_CHARS = 1200

_INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"(reveal|print|show|repeat|leak).{0,30}(system prompt|instructions|api key|secret|\.env)",
    r"\byou are now\b",
    r"\b(developer|dan|jailbreak|god) mode\b",
    r"\bact as (an?|the) (?!photographer|photo)",
    r"\bnew instructions?\s*:",
    r"\bsystem\s*:\s",
    r"portkey_api_key|openai_api_key|sk-[a-z0-9]{8,}",
]

# Requests to photograph people covertly or sexually without consent are refused.
_VOYEUR_PATTERNS = [
    r"without (them|her|him|people|anyone) (knowing|noticing|seeing)",
    r"\b(hidden|spy|secret(ly)?|covert(ly)?) (camera|photos?|shots?|pictures?)\b",
    r"\bup[- ]?skirt\b|\bdown[- ]?blouse\b",
    r"through (a |the |their |her |his )?(bedroom |bathroom )?window",
    r"\b(changing room|locker room|bathroom stall)\b",
    r"\bnudes? of (a|my|the|someone)",
]

_OFF_TOPIC_HINTS = [
    r"\b(write|draft) (me )?(an? )?(essay|email|poem|code|program|cover letter)\b",
    r"\b(stock|crypto|bitcoin) (tip|price|advice)\b",
    r"\bhomework\b",
]

# Physical-risk keywords -> advisory shown with the settings.
_RISK_RULES = [
    (r"\b(cliff|ledge|edge of|overhang|drop[- ]?off|precipice)\b",
     "Cliff edges: stay well back from the edge and never step backward while framing. Have your friend move, not you."),
    (r"\b(rail(way|road)? tracks?|train tracks?|on the tracks)\b",
     "Never pose or set up on railway tracks: it is illegal trespassing in the US and trains can't stop. Shoot from a legal crossing or platform."),
    (r"\b(middle of the (road|street|highway)|on the highway|crosswalk)\b",
     "Don't stand in traffic lanes for a shot. Use a sidewalk or median and a longer lens instead."),
    (r"\b(roof ?top|rooftop|on the roof|scaffold)\b",
     "Rooftops: only where access is permitted and there is a railing; never sit on parapets."),
    (r"\b(waterfall|rapids|wet rocks?|slippery|tide ?pools?|jetty|breakwater)\b",
     "Wet rock is the #1 cause of photographer falls near water. Wear grippy shoes and keep a hand free."),
    (r"\b(ice|frozen (lake|pond|river))\b",
     "Do not walk on frozen water unless ice thickness is officially posted as safe."),
    (r"\b(bear|moose|bison|elk|wildlife|wild animals?)\b",
     "Wildlife: keep 100 yards from bears and 25+ yards from moose/bison/elk (US National Park Service guidance). Use a telephoto lens rather than getting close."),
    (r"\b(drone)\b",
     "Drones: US National Parks ban drone launches, and airspace near airports needs FAA authorization. Check the B4UFLY app before flying."),
    (r"\b(lightning|thunder ?storm|storm chasing)\b",
     "Lightning: if you can hear thunder you are within striking distance. Shoot from inside a car or building."),
    (r"\b(stars?|milky ?way|night (hike|sky)|astro|aurora|northern lights)\b",
     "Night shoots: bring a red-light headlamp, tell someone where you're going, and scout the spot in daylight."),
    (r"\b(sun|solar|eclipse)\b.*\b(directly|straight at|eclipse)\b|\beclipse\b",
     "Never point a camera's optical viewfinder at the sun; use a certified solar filter for eclipses."),
]


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    category: str = ""
    advisories: list[str] = field(default_factory=list)


def log_event(kind: str, detail: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": kind, **detail}
    with (LOGS_DIR / "safety.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _matches(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def check_prompt(text: str, exif_free_text: dict | None = None) -> GuardResult:
    """Deterministic guard run before any model call (zero tokens spent on attacks)."""

    text = (text or "").strip()
    if len(text) > MAX_PROMPT_CHARS:
        log_event("blocked_too_long", {"chars": len(text)})
        return GuardResult(False, f"Please keep the description under {MAX_PROMPT_CHARS} characters.", "length")

    hit = _matches(_INJECTION_PATTERNS, text)
    if hit:
        log_event("blocked_prompt_injection", {"source": "prompt", "match": hit, "text": text[:200]})
        return GuardResult(False, "This looks like an attempt to change ShotCoach's instructions, so it was blocked. Describe the photo you want instead.", "prompt_injection")

    for tag, value in (exif_free_text or {}).items():
        hit = _matches(_INJECTION_PATTERNS, value)
        if hit:
            log_event("blocked_prompt_injection", {"source": f"exif:{tag}", "match": hit, "text": value[:200]})
            return GuardResult(False, f"The uploaded image's metadata ({tag}) contains hidden instructions, so it was rejected. Re-save or screenshot the photo and try again.", "prompt_injection")

    hit = _matches(_VOYEUR_PATTERNS, text)
    if hit:
        log_event("blocked_privacy", {"match": hit, "text": text[:200]})
        return GuardResult(False, "ShotCoach doesn't help photograph people secretly or without consent.", "privacy")

    hit = _matches(_OFF_TOPIC_HINTS, text)
    if hit:
        log_event("blocked_off_topic", {"match": hit, "text": text[:200]})
        return GuardResult(False, "ShotCoach only helps with taking photos. Describe a shot you'd like to take.", "off_topic")

    return GuardResult(True, advisories=risk_advisories(text))


def risk_advisories(text: str) -> list[str]:
    notes = []
    for pat, note in _RISK_RULES:
        if re.search(pat, text or "", flags=re.IGNORECASE) and note not in notes:
            notes.append(note)
    if re.search(r"\b(kids?|child(ren)?|toddlers?|baby|babies|minors?|strangers?|crowd)\b", text or "", re.I):
        notes.append("People you don't know (especially children): ask permission before photographing or posting them.")
    return notes


def weather_advisories(conditions: dict | None) -> list[str]:
    notes = []
    w = (conditions or {}).get("weather")
    if not isinstance(w, dict):
        return notes
    if max(v["wind_kmh"] for v in w.values()) >= 40:
        notes.append("Strong wind forecast (40+ km/h): weigh down the tripod and keep away from exposed edges.")
    if min(v["temp_c"] for v in w.values()) <= 0:
        notes.append("Below-freezing temperatures: batteries drain fast; keep spares in an inside pocket.")
    return notes


# --- Output validation -------------------------------------------------------

def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, str(text or ""))
    return float(m.group(1)) if m else None


def validate_settings(settings: dict, gear: dict, shot_type: str, focal_mm: float | None) -> list[str]:
    """Check the model's settings against the real limits of the user's gear.

    Fixes impossible values in place and returns human-readable corrections.
    """

    fixes: list[str] = []
    core = settings.setdefault("core", {})

    if gear["camera_type"] != "phone":
        ap = _num(r"f/?\s*([\d.]+)", core.get("aperture"))
        if ap is not None:
            widest = gear["max_aperture"]
            if focal_mm and gear["max_focal_mm"] > gear["min_focal_mm"]:
                # Variable-aperture zooms get slower toward the long end.
                frac = (focal_mm - gear["min_focal_mm"]) / (gear["max_focal_mm"] - gear["min_focal_mm"])
                widest = gear["max_aperture"] + max(0, min(1, frac)) * (gear["max_aperture_tele"] - gear["max_aperture"])
                widest = calc.snap_aperture(widest)
            if ap < widest - 0.05:
                fixes.append(f"Aperture f/{ap:g} is wider than your lens allows at this focal length; changed to f/{widest:g}.")
                core["aperture"] = f"f/{widest:g}"
            elif ap > gear["min_aperture"] + 0.05:
                fixes.append(f"Aperture f/{ap:g} is beyond your lens's smallest aperture; changed to f/{gear['min_aperture']:g}.")
                core["aperture"] = f"f/{gear['min_aperture']:g}"

    iso = _num(r"(\d{2,6})", core.get("iso"))
    if iso is not None and iso > gear["max_iso"]:
        fixes.append(f"ISO {iso:g} exceeds your camera's native maximum; capped at ISO {gear['max_iso']}.")
        core["iso"] = str(gear["max_iso"])

    shutter_text = str(core.get("shutter") or "")
    try:
        shutter_s = calc.parse_shutter(re.search(r"(\d+(?:\.\d+)?(?:/\d+)?)", shutter_text).group(1)) if shutter_text else None
    except (AttributeError, ValueError, ZeroDivisionError):
        shutter_s = None
    if shutter_s:
        fastest = calc.parse_shutter(gear["fastest_shutter"])
        if shutter_s < fastest * 0.99:
            fixes.append(f"Shutter {shutter_text} is faster than your camera's {gear['fastest_shutter']}s limit; changed.")
            core["shutter"] = calc.format_shutter(fastest)
        if shot_type == "astro" and focal_mm and gear["camera_type"] != "phone":
            ap = _num(r"f/?\s*([\d.]+)", core.get("aperture")) or gear["max_aperture"]
            limit = calc.star_shutter_limit(focal_mm, ap, gear["crop"], gear["pixel_pitch_um"])
            max_s = calc.parse_shutter(limit["recommended_max_shutter"].rstrip("s"))
            if shutter_s > max_s * 1.05:
                fixes.append(f"Shutter {shutter_text} would make stars trail at {focal_mm:g}mm; shortened to {limit['recommended_max_shutter']} (NPF rule).")
                core["shutter"] = limit["recommended_max_shutter"]

    if fixes:
        log_event("output_corrected", {"camera": gear["camera_name"], "fixes": fixes})
    return fixes


# --- Rate limiting -----------------------------------------------------------

_RECENT: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = 8  # runs
RATE_WINDOW_S = 60


def rate_limited(client_id: str) -> bool:
    now = time.time()
    q = _RECENT[client_id]
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        log_event("rate_limited", {"client": client_id[:12]})
        return True
    q.append(now)
    return False

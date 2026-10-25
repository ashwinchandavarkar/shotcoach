"""The four ShotCoach agents and the orchestrator that runs them.

Pipeline for "Plan a shot":
  Safety gate (code) -> Scene Analyst (LLM, vision) -> Conditions Scout (code + free APIs)
  -> Settings Engineer (LLM + calculator tools) -> Output validator (code)
  -> Hand-off Coach (LLM, only when the user will be in the photo)

Pipeline for "Review my shot":
  Safety gate (code, incl. EXIF scan) -> EXIF reader (code) -> Shot Critic (LLM, vision)
"""

from __future__ import annotations

import datetime as dt
import json
import time

from . import calculators as calc
from . import conditions as cond
from . import safety
from .config import PROMPTS_DIR
from .gear import GearProfile
from .llm import RunTracker, call_json, call_with_tools


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _gear_summary(gear: GearProfile) -> str:
    return json.dumps(gear.to_dict(), indent=1)


# --- Calculator tools ----------------------------------------------------------

def _num_or_null(desc: str) -> dict:
    return {"type": ["number", "null"], "description": desc}


def _tool(name: str, desc: str, props: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
        "strict": True,
    }


_FOCAL = {"type": "number", "description": "Focal length in mm as marked on the lens (phones: 35mm-equivalent)."}
_AP = {"type": "number", "description": "f-number, e.g. 8 for f/8"}

TOOLS = [
    _tool("solve_exposure", "Solve the exposure triangle for a scene: give two of aperture/shutter/iso (null for the unknown).", {
        "scene_light": {"type": "string", "enum": list(calc.SCENE_EV100)},
        "aperture": _num_or_null("f-number or null"),
        "shutter": {"type": ["string", "null"], "description": "e.g. '1/250' or '15' seconds, or null"},
        "iso": _num_or_null("ISO or null"),
        "exposure_compensation": {"type": "number", "description": "EV, usually 0; -0.7 for bright skies, +0.7 for snow"},
    }),
    _tool("depth_of_field", "Near/far limits of sharpness for a focus distance.", {
        "focal_mm": _FOCAL, "aperture": _AP,
        "subject_distance_m": {"type": "number", "description": "Focus distance in metres"},
    }),
    _tool("hyperfocal", "Hyperfocal distance for maximum front-to-back sharpness in landscapes.", {
        "focal_mm": _FOCAL, "aperture": _AP,
    }),
    _tool("star_shutter_limit", "Longest shutter before stars trail (500 and NPF rules).", {
        "focal_mm": _FOCAL, "aperture": _AP,
    }),
    _tool("motion_freeze_shutter", "Slowest shutter that freezes a moving subject.", {
        "subject_speed_kmh": {"type": "number"},
        "subject_distance_m": {"type": "number"},
        "focal_mm": _FOCAL,
        "direction": {"type": "string", "enum": ["across", "diagonal", "toward"]},
    }),
    _tool("handheld_min_shutter", "Slowest shutter for sharp handheld shots on this camera.", {
        "focal_mm": _FOCAL,
    }),
]


def make_dispatch(gear: GearProfile):
    """Bind calculator tools to the user's gear so the model can't pass wrong sensor data."""

    def real_focal(f: float) -> float:
        return f / gear.crop if gear.is_phone else f

    def dispatch(name: str, a: dict) -> dict:
        if name == "solve_exposure":
            return calc.solve_exposure(a["scene_light"], a.get("aperture"), a.get("shutter"), a.get("iso"), a.get("exposure_compensation") or 0)
        if name == "depth_of_field":
            return calc.depth_of_field(real_focal(a["focal_mm"]), a["aperture"], a["subject_distance_m"], gear.crop)
        if name == "hyperfocal":
            return calc.hyperfocal(real_focal(a["focal_mm"]), a["aperture"], gear.crop)
        if name == "star_shutter_limit":
            return calc.star_shutter_limit(real_focal(a["focal_mm"]), a["aperture"], gear.crop, gear.pixel_pitch_um)
        if name == "motion_freeze_shutter":
            return calc.motion_freeze_shutter(a["subject_speed_kmh"], a["subject_distance_m"], real_focal(a["focal_mm"]), gear.pixel_pitch_um, gear.crop, a.get("direction", "across"))
        if name == "handheld_min_shutter":
            return calc.handheld_min_shutter(real_focal(a["focal_mm"]), gear.crop, gear.ibis)
        return {"error": f"unknown tool {name}"}

    return dispatch


# --- Agents --------------------------------------------------------------------

def scene_analyst(description: str, image_url: str | None, gear: GearProfile, tracker: RunTracker) -> dict:
    instructions = _prompt("scene_analyst").format(scene_lights=", ".join(calc.SCENE_EV100), gear=_gear_summary(gear))
    content = [{"type": "input_text", "text": f"<user_request>\n{description or '(no text; use the reference photo)'}\n</user_request>"}]
    if image_url:
        content.append({"type": "input_text", "text": "Reference photo the user wants to recreate:"})
        content.append({"type": "input_image", "image_url": image_url, "detail": "low"})
    return call_json("Scene Analyst", instructions, content, tracker)


def settings_engineer(brief: dict, gear: GearProfile, conditions: dict | None, tracker: RunTracker) -> tuple[dict, list[dict]]:
    payload = {"shot_brief": brief, "gear_profile": gear.to_dict(), "conditions_report": conditions or "No location given."}
    content = [{"type": "input_text", "text": json.dumps(payload, indent=1)}]
    return call_with_tools("Settings Engineer", _prompt("settings_engineer"), content, TOOLS, make_dispatch(gear), tracker)


def handoff_coach(brief: dict, settings: dict, gear: GearProfile, owner_name: str, tracker: RunTracker) -> dict:
    name = (owner_name or "me").strip()[:30] or "me"
    instructions = _prompt("handoff_coach").replace("{name}", name)
    payload = {"owner_name": name, "shot_brief": brief, "camera": gear.camera_name, "settings": settings.get("core"), "focal_length_mm": settings.get("focal_length_mm")}
    content = [{"type": "input_text", "text": json.dumps(payload, indent=1)}]
    return call_json("Hand-off Coach", instructions, content, tracker)


def shot_critic(goal: str, image_url: str, exif: dict, gear: GearProfile, tracker: RunTracker) -> dict:
    # EXIF focal length is the real one; for phones the profile stores 35mm-equivalent.
    focal = exif.get("focal_length_mm") or (gear.min_focal_mm / gear.crop if gear.is_phone else gear.min_focal_mm)
    limits = {"handheld": calc.handheld_min_shutter(focal, gear.crop, gear.ibis)}
    payload = {"what_user_wanted": goal or "(not stated)", "exif_settings": exif or "No EXIF found", "gear_profile": gear.to_dict(), "calculator_results": limits}
    content = [
        {"type": "input_text", "text": json.dumps(payload, indent=1)},
        {"type": "input_image", "image_url": image_url, "detail": "high"},
    ]
    return call_json("Shot Critic", _prompt("shot_critic"), content, tracker)


# --- Orchestrators ------------------------------------------------------------

def plan_shot(
    description: str,
    gear: GearProfile,
    image_url: str | None = None,
    image_free_text: dict | None = None,
    place: str | None = None,
    date: dt.date | None = None,
    in_photo: bool | None = None,
    owner_name: str = "",
) -> dict:
    """Run the full planning pipeline and return everything the UI shows."""

    steps: list[dict] = []

    def step(name: str, kind: str, detail: str, t0: float) -> None:
        steps.append({"agent": name, "kind": kind, "detail": detail, "seconds": round(time.time() - t0, 2)})

    t0 = time.time()
    guard = safety.check_prompt(f"{description}\n{place or ''}", image_free_text)
    step("Safety Gate", "code", "passed" if guard.allowed else f"blocked: {guard.category}", t0)
    if not guard.allowed:
        return {"blocked": True, "reason": guard.reason, "category": guard.category, "steps": steps}

    tracker = RunTracker("plan")
    t0 = time.time()
    brief = scene_analyst(description, image_url, gear, tracker)
    step("Scene Analyst", "llm", f"{brief.get('shot_type')} - {brief.get('title')}", t0)
    if brief.get("injection_suspected") or brief.get("in_scope") is False:
        cat = "prompt_injection" if brief.get("injection_suspected") else "off_topic"
        safety.log_event(f"blocked_{cat}", {"source": "scene_analyst_llm", "text": (description or "")[:200]})
        return {
            "blocked": True,
            "category": cat,
            "reason": "ShotCoach only helps plan photos, and this request (or text inside the image) looked like something else.",
            "steps": steps,
            "usage": tracker.summary(),
        }
    if in_photo is not None:
        brief["person_in_frame"] = bool(in_photo) or brief.get("person_in_frame", False)

    conditions = None
    place = (place or brief.get("location_hint") or "").strip()
    if place:
        t0 = time.time()
        try:
            conditions = cond.scout(place, date)
            step("Conditions Scout", "code+api", conditions.get("place") or conditions.get("error", ""), t0)
        except Exception as exc:  # noqa: BLE001 - network failures must not break the run
            conditions = {"error": f"Conditions unavailable ({type(exc).__name__})."}
            step("Conditions Scout", "code+api", conditions["error"], t0)

    t0 = time.time()
    settings, trace = settings_engineer(brief, gear, conditions if conditions and "error" not in conditions else None, tracker)
    step("Settings Engineer", "llm+tools", f"{len(trace)} calculator calls", t0)

    t0 = time.time()
    focal = settings.get("focal_length_mm") or brief.get("suggested_focal_mm")
    if gear.is_phone and focal:
        focal = focal / gear.crop
    fixes = safety.validate_settings(settings, gear.to_dict(), brief.get("shot_type", ""), focal)
    step("Output Validator", "code", f"{len(fixes)} correction(s)", t0)

    handoff = None
    if brief.get("person_in_frame"):
        t0 = time.time()
        handoff = handoff_coach(brief, settings, gear, owner_name, tracker)
        step("Hand-off Coach", "llm", handoff.get("headline", ""), t0)

    advisories = guard.advisories + safety.risk_advisories(json.dumps(brief)) + safety.weather_advisories(conditions)
    advisories = list(dict.fromkeys(advisories))

    return {
        "blocked": False,
        "brief": brief,
        "conditions": conditions,
        "settings": settings,
        "tool_trace": trace,
        "fixes": fixes,
        "handoff": handoff,
        "advisories": advisories,
        "steps": steps,
        "usage": tracker.summary(),
        "gear": gear.to_dict(),
    }


def review_shot(goal: str, gear: GearProfile, image_url: str, exif: dict, exif_free_text: dict) -> dict:
    steps = []
    t0 = time.time()
    guard = safety.check_prompt(goal, exif_free_text)
    steps.append({"agent": "Safety Gate", "kind": "code", "detail": "passed" if guard.allowed else f"blocked: {guard.category}", "seconds": round(time.time() - t0, 2)})
    if not guard.allowed:
        return {"blocked": True, "reason": guard.reason, "category": guard.category, "steps": steps}
    tracker = RunTracker("review")
    t0 = time.time()
    critique = shot_critic(goal, image_url, exif, gear, tracker)
    steps.append({"agent": "Shot Critic", "kind": "llm", "detail": critique.get("verdict", ""), "seconds": round(time.time() - t0, 2)})
    return {"blocked": False, "critique": critique, "exif": exif, "steps": steps, "usage": tracker.summary()}

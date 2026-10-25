"""Camera and lens database plus the user's resolved gear profile."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache

from .config import DATA_DIR


@lru_cache(maxsize=1)
def _db() -> dict:
    return json.loads((DATA_DIR / "gear.json").read_text(encoding="utf-8"))


def cameras() -> list[dict]:
    return _db()["cameras"]


def lenses() -> list[dict]:
    return _db()["lenses"]


def camera_by_id(camera_id: str) -> dict:
    for cam in cameras():
        if cam["id"] == camera_id:
            return cam
    raise KeyError(f"Unknown camera: {camera_id}")


def lens_by_id(lens_id: str) -> dict | None:
    for lens in lenses():
        if lens["id"] == lens_id:
            return lens
    return None


def controls_for(brand: str) -> dict:
    """Brand-specific names for modes and menus (generic bodies use Sony-style A/S/M)."""

    table = _db()["brand_controls"]
    return table.get(brand) or {
        key: value.replace("Sony ", "") for key, value in table["Sony"].items()
    }


@dataclass
class GearProfile:
    camera_id: str
    camera_name: str
    brand: str
    camera_type: str
    sensor: str
    crop: float
    sensor_width_mm: float
    pixel_pitch_um: float
    max_iso: int
    fastest_shutter: str
    ibis: bool
    lens_name: str
    min_focal_mm: float
    max_focal_mm: float
    max_aperture: float
    max_aperture_tele: float
    min_aperture: float

    @property
    def is_phone(self) -> bool:
        return self.camera_type == "phone"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["controls"] = controls_for(self.brand)
        return data


def build_profile(
    camera_id: str,
    lens_id: str | None = None,
    custom_min_focal: float | None = None,
    custom_max_focal: float | None = None,
    custom_max_aperture: float | None = None,
) -> GearProfile:
    cam = camera_by_id(camera_id)
    fixed = cam.get("fixed_lens")
    if fixed:
        lens = {
            "name": f"Built-in {fixed['min_focal']}mm f/{fixed['max_aperture']} lens",
            "min_focal": fixed["min_focal"],
            "max_focal": fixed["max_focal"],
            "max_aperture": fixed["max_aperture"],
            "max_aperture_tele": fixed["max_aperture"],
            "min_aperture": fixed["max_aperture"] if cam["type"] == "phone" else 16,
        }
    elif lens_id == "custom" and custom_min_focal and custom_max_aperture:
        max_focal = custom_max_focal or custom_min_focal
        lens = {
            "name": f"Custom {custom_min_focal:g}-{max_focal:g}mm f/{custom_max_aperture:g}",
            "min_focal": custom_min_focal,
            "max_focal": max_focal,
            "max_aperture": custom_max_aperture,
            "max_aperture_tele": custom_max_aperture,
            "min_aperture": 22,
        }
    else:
        lens = lens_by_id(lens_id or "") or lens_by_id("kit-1855")

    pitch_um = cam["sensor_width_mm"] / cam["h_pixels"] * 1000
    return GearProfile(
        camera_id=cam["id"],
        camera_name=cam["name"],
        brand=cam["brand"],
        camera_type=cam["type"],
        sensor=cam["sensor"],
        crop=cam["crop"],
        sensor_width_mm=cam["sensor_width_mm"],
        pixel_pitch_um=round(pitch_um, 2),
        max_iso=cam["max_iso"],
        fastest_shutter=f"1/{cam['fastest_shutter']}",
        ibis=cam["ibis"],
        lens_name=lens["name"],
        min_focal_mm=lens["min_focal"],
        max_focal_mm=lens["max_focal"],
        max_aperture=lens["max_aperture"],
        max_aperture_tele=lens["max_aperture_tele"],
        min_aperture=lens["min_aperture"],
    )

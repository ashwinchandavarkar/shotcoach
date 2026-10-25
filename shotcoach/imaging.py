"""Image handling: decode uploads, read EXIF safely, shrink before sending to the model."""

from __future__ import annotations

import base64
import io

from PIL import ExifTags, Image, ImageOps

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MODEL_IMAGE_MAX_PX = 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}

# Only numeric camera settings plus make/model/lens reach the model. Free-text
# fields (ImageDescription, UserComment, XPComment, Artist...) can carry hidden
# instructions, so they are read only to be scanned by the safety layer.
_SETTING_TAGS = {
    "Make", "Model", "LensModel", "ExposureTime", "FNumber", "ISOSpeedRatings",
    "PhotographicSensitivity", "FocalLength", "FocalLengthIn35mmFilm",
    "ExposureProgram", "ExposureBiasValue", "MeteringMode", "Flash", "WhiteBalance",
    "DateTimeOriginal",
}
_FREE_TEXT_TAGS = {"ImageDescription", "UserComment", "XPComment", "XPTitle", "XPSubject", "Artist", "Copyright", "Software"}
_EXPOSURE_PROGRAMS = {
    0: "Not defined", 1: "Manual", 2: "Program (auto)", 3: "Aperture priority",
    4: "Shutter priority", 5: "Creative", 6: "Action", 7: "Portrait", 8: "Landscape",
}


class ImageError(ValueError):
    pass


def decode_upload(contents: str) -> tuple[Image.Image, bytes]:
    """Turn a Dash dcc.Upload data URL into a verified PIL image."""

    try:
        header, b64 = contents.split(",", 1)
        raw = base64.b64decode(b64)
    except (ValueError, AttributeError) as exc:
        raise ImageError("That upload could not be read.") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageError("Image is larger than 12 MB. Please upload a smaller copy.")
    try:
        Image.open(io.BytesIO(raw)).verify()
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types
        raise ImageError("That file is not a valid image.") from exc
    if img.format not in ALLOWED_FORMATS:
        raise ImageError("Please upload a JPEG, PNG or WEBP photo.")
    return img, raw


def _clean(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return text.strip()[:200]


def _f(value) -> float:
    """EXIF numbers arrive as IFDRational, int, or (numerator, denominator) tuples."""

    if isinstance(value, tuple) and len(value) == 2:
        return value[0] / value[1] if value[1] else 0.0
    return float(value)


def read_exif(img: Image.Image) -> tuple[dict, dict]:
    """Return (camera_settings, free_text_fields)."""

    exif = img.getexif()
    merged = dict(exif.items())
    try:
        merged.update(exif.get_ifd(0x8769).items())  # Exif sub-IFD holds the settings
    except Exception:  # noqa: BLE001
        pass
    settings, free_text = {}, {}
    for tag_id, value in merged.items():
        name = ExifTags.TAGS.get(tag_id, str(tag_id))
        if name in _SETTING_TAGS:
            settings[name] = value
        elif name in _FREE_TEXT_TAGS:
            free_text[name] = _clean(value)

    out = {}
    if "Make" in settings or "Model" in settings:
        make, model = _clean(settings.get("Make", "")), _clean(settings.get("Model", ""))
        out["camera"] = (model if model.lower().startswith(make.lower()) else f"{make} {model}").strip()[:60]
    if "LensModel" in settings:
        out["lens"] = _clean(settings["LensModel"])[:60]
    if "ExposureTime" in settings:
        t = _f(settings["ExposureTime"])
        out["shutter"] = f"1/{round(1 / t)}s" if 0 < t < 0.3 else f"{t:g}s"
    if "FNumber" in settings:
        out["aperture"] = f"f/{_f(settings['FNumber']):g}"
    iso = settings.get("ISOSpeedRatings") or settings.get("PhotographicSensitivity")
    if iso:
        out["iso"] = int(iso[0] if isinstance(iso, tuple) else iso)
    if "FocalLength" in settings:
        out["focal_length_mm"] = round(_f(settings["FocalLength"]), 1)
    if "FocalLengthIn35mmFilm" in settings:
        out["focal_length_35mm_equiv"] = int(settings["FocalLengthIn35mmFilm"])
    if "ExposureProgram" in settings:
        out["mode"] = _EXPOSURE_PROGRAMS.get(int(settings["ExposureProgram"]), "Unknown")
    if "ExposureBiasValue" in settings:
        out["exposure_compensation_ev"] = round(_f(settings["ExposureBiasValue"]), 1)
    if "Flash" in settings:
        out["flash_fired"] = bool(int(settings["Flash"]) & 1)
    if "DateTimeOriginal" in settings:
        out["taken_at"] = _clean(settings["DateTimeOriginal"])[:20]
    return out, free_text


def to_model_data_url(img: Image.Image) -> str:
    """Re-encode as a small, metadata-free JPEG (cuts image tokens and strips EXIF)."""

    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MODEL_IMAGE_MAX_PX, MODEL_IMAGE_MAX_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def to_preview_data_url(img: Image.Image, max_px: int = 640) -> str:
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

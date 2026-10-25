"""Draws the Hand-off Card framing sketch as SVG from the Hand-off Coach's JSON.

Drawing is deterministic code, not image generation: the model only decides
positions (thirds, horizon, size, group size) and this module renders them.
"""

from __future__ import annotations

import base64
from html import escape

YELLOW = "#FFD60A"
MUTED = "#8a8a8a"
TEXT = "#e6e6e6"


def _text(x, y, label, anchor="middle", fill=TEXT, size=12, weight=400) -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" fill="{fill}" font-size="{size}" '
            f'font-weight="{weight}" font-family="sans-serif" paint-order="stroke" stroke="#1d1d1d" '
            f'stroke-width="3">{escape(label)}</text>')


def _person(cx: float, feet_y: float, ph: float) -> str:
    head_r = ph * 0.11
    head_cy = feet_y - ph + head_r
    body_w = head_r * 2.6
    body_top = head_cy + head_r * 1.15
    return (f'<circle cx="{cx:.0f}" cy="{head_cy:.0f}" r="{head_r:.0f}" fill="{YELLOW}"/>'
            f'<rect x="{cx - body_w / 2:.0f}" y="{body_top:.0f}" width="{body_w:.0f}" '
            f'height="{max(feet_y - body_top, 4):.0f}" rx="{head_r * 0.8:.0f}" fill="{YELLOW}"/>')


def framing_svg(card: dict, name: str = "You") -> str:
    portrait = str(card.get("orientation", "landscape")).startswith("portrait")
    w, h = (240, 360) if portrait else (360, 240)

    horizon_y = {"upper_third": h / 3, "middle": h / 2, "lower_third": 2 * h / 3}.get(card.get("horizon"))
    subj_x = {"left_third": w / 3, "center": w / 2, "right_third": 2 * w / 3}.get(card.get("subject_position"), w / 3)
    size = {"full_body": 0.62, "half_body": 0.85, "head_shoulders": 1.1, "small_in_scene": 0.3}.get(card.get("subject_size"), 0.5)
    try:
        people = max(1, min(int(card.get("people_count") or 1), 8))
    except (TypeError, ValueError):
        people = 1
    ph = h * size * (0.8 if people > 3 else 1.0)
    feet_y = h * 0.92 if card.get("subject_size") in ("full_body", "small_in_scene") else h * 1.05

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%">',
             f'<rect width="{w}" height="{h}" rx="10" fill="#1d1d1d" stroke="{YELLOW}" stroke-width="3"/>']
    if horizon_y:
        parts.append(f'<rect x="1.5" y="{horizon_y:.0f}" width="{w - 3}" height="{h - horizon_y - 1.5:.0f}" fill="#262626"/>')

    # Landmark: wide mountain shape when "behind"/"top", a tower at the side otherwise.
    lm = card.get("landmark_position")
    label = str(card.get("landmark_label") or "Landmark")[:20]
    base_y = horizon_y or h * 0.7
    if lm in ("behind", "top"):
        parts.append(f'<polygon points="0,{base_y:.0f} {w * 0.3:.0f},{base_y - h * 0.4:.0f} {w * 0.55:.0f},{base_y - h * 0.18:.0f} '
                     f'{w * 0.75:.0f},{base_y - h * 0.33:.0f} {w},{base_y - h * 0.1:.0f} {w},{base_y:.0f}" fill="#3a3a3a"/>')
        # Label on the peak farthest from the people.
        lx = w * 0.75 if subj_x < w / 2 else w * 0.3
        parts.append(_text(lx, base_y - h * (0.33 if subj_x < w / 2 else 0.4) - 6, label))
    elif lm in ("left", "right"):
        lx = w / 8 if lm == "left" else 7 * w / 8
        lh = min(h * 0.38, base_y - 24)  # keep the label inside the frame
        parts.append(f'<polygon points="{lx - 12:.0f},{base_y:.0f} {lx + 12:.0f},{base_y:.0f} {lx + 6:.0f},{base_y - lh:.0f} '
                     f'{lx - 6:.0f},{base_y - lh:.0f}" fill="{MUTED}"/>')
        parts.append(_text(lx, base_y - lh - 8, label))

    if horizon_y:
        parts.append(f'<line x1="0" y1="{horizon_y:.0f}" x2="{w}" y2="{horizon_y:.0f}" stroke="#cfcfcf" stroke-width="2"/>')
    for i in (1, 2):
        parts.append(f'<line x1="{w * i / 3:.0f}" y1="0" x2="{w * i / 3:.0f}" y2="{h}" stroke="{YELLOW}" stroke-opacity=".35" stroke-dasharray="5 5"/>')
        parts.append(f'<line x1="0" y1="{h * i / 3:.0f}" x2="{w}" y2="{h * i / 3:.0f}" stroke="{YELLOW}" stroke-opacity=".35" stroke-dasharray="5 5"/>')

    # People: spread a group evenly around the subject position.
    spacing = ph * 0.32
    group_w = spacing * (people - 1)
    start = min(max(subj_x - group_w / 2, ph * 0.15 + 4), w - group_w - ph * 0.15 - 4)
    for i in range(people):
        parts.append(_person(start + i * spacing, feet_y, ph))

    name_label = name[:14] if people == 1 else f"{name[:12]} + {people - 1}"
    top = feet_y - ph
    parts.append(_text(start + group_w / 2, max(top - 6, 14), name_label, fill=YELLOW, size=13, weight=700))

    if horizon_y:
        # Horizon hint goes on the side away from the people.
        hx, anchor = (w - 8, "end") if start + group_w / 2 < w / 2 else (8, "start")
        parts.append(_text(hx, horizon_y + 15, "keep horizon level", anchor=anchor, fill="#cfcfcf", size=11))
    parts.append("</svg>")
    return "".join(parts)


def svg_data_url(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()

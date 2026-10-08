"""Drawing the inspection views (display only; geometry is never resampled except by an
integer zoom factor).

Coordinates: canonical pixels (0-based, pixel centres at integers, y down). In the zoom
view a crop starting at ``(x0, y0)`` and enlarged ``k`` times by pixel replication maps
canonical ``x`` to ``(x - x0) * k + (k - 1) / 2`` (the centre of the replicated block), so
markers stay exactly on their pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from PIL import Image, ImageDraw

from astroidentify.astrometry.wcs import pixel_to_sky, sky_to_pixel
from astroidentify.inspection.references import ReferenceStar
from astroidentify.objects.overlay import _font

OBJECT_COLOR = (255, 60, 220)
STAR_COLOR = (90, 220, 255)
TEXT_COLOR = (240, 240, 240)
OUTLINE = (0, 0, 0)


@dataclass(frozen=True)
class View:
    """Mapping from canonical pixels to view pixels: ``v = (c - origin) * k + (k - 1) / 2``."""

    x0: int
    y0: int
    zoom: int

    def to_view(self, x: float, y: float) -> tuple[float, float]:
        k = self.zoom
        return (x - self.x0) * k + (k - 1) / 2, (y - self.y0) * k + (k - 1) / 2


def preferred_common_name(names) -> str | None:
    """The common name to show, chosen generically from SIMBAD's ``NAME`` identifiers.

    Mixed-case names come first (SIMBAD's all-capitals entries are typically legacy
    abbreviations), then the longest (most complete) name, then SIMBAD's own order.
    """
    names = [n for n in (names or ()) if n and n.strip()]
    if not names:
        return None
    ranked = sorted(enumerate(names), key=lambda item: (item[1].isupper(), -len(item[1]), item[0]))
    return ranked[0][1]


def object_label(obj: dict, requested_name: str | None = None) -> dict:
    """Overlay label of the inspected object (presentation only; identity is unchanged).

    With a SIMBAD common name: the name, then the catalogue designation (display name, plus
    the main ID if different). Without one: the designation, then the SIMBAD type. If the
    object was selected by one of its common names (``requested_name``), that spelling is
    shown, since SIMBAD may list several variants of the same name.
    """
    designation = obj["display_name"]
    if obj.get("main_id") and obj["main_id"] != obj["display_name"]:
        designation += f" [{obj['main_id']}]"
    names = list(obj.get("common_names") or ())
    if requested_name in names:
        names = [requested_name]
    common = preferred_common_name(names)
    if common is not None:
        return {"primary": common, "secondary": designation, "source": "simbad_common_name"}
    kind = obj.get("object_type_description") or obj.get("object_type") or ""
    return {"primary": designation, "secondary": kind, "source": "display_name"}


def orientation(wcs: WCS, x: float, y: float, step_arcsec: float = 60.0):
    """Unit pixel vectors toward north and east at ``(x, y)`` (from the WCS, not assumed)."""
    ra, dec = pixel_to_sky(wcs, np.array([x]), np.array([y]))
    step = step_arcsec / 3600.0
    north = sky_to_pixel(wcs, ra, dec + step)
    east = sky_to_pixel(wcs, ra + step / math.cos(math.radians(float(dec[0]))), dec)
    vectors = []
    for px, py in (north, east):
        dx, dy = float(px[0]) - x, float(py[0]) - y
        norm = math.hypot(dx, dy)
        vectors.append((dx / norm, dy / norm))
    return vectors[0], vectors[1]


def nice_scale(pixel_scale_arcsec: float, target_px: float) -> tuple[float, str]:
    """A round angular length close to ``target_px`` pixels: ``(length_px, label)``."""
    target = target_px * pixel_scale_arcsec
    for arcsec, label in (
        (5, '5"'),
        (10, '10"'),
        (30, '30"'),
        (60, "1'"),
        (120, "2'"),
        (300, "5'"),
        (600, "10'"),
        (1200, "20'"),
        (1800, "30'"),
        (3600, "1 deg"),
        (7200, "2 deg"),
    ):
        if arcsec >= target * 0.6:
            return arcsec / pixel_scale_arcsec, label
    return 7200 / pixel_scale_arcsec, "2 deg"


def render_view(
    pixels: np.ndarray,
    view: View,
    *,
    wcs: WCS,
    obj: dict,
    outline: list[tuple[float, float]] | None,
    stars: list[ReferenceStar],
    label_stars: bool,
    pixel_scale_arcsec: float,
    notes: list[str],
    requested_name: str | None = None,
) -> Image.Image:
    """Draw object, outline, reference stars, scale bar, N/E arrows and notes."""
    image = Image.fromarray(np.ascontiguousarray(pixels)).convert("RGB")
    if view.zoom > 1:
        image = image.resize((image.width * view.zoom, image.height * view.zoom), Image.NEAREST)
    draw = ImageDraw.Draw(image)
    width, height = image.size
    scale = min(width, height)
    line = max(1, round(scale / 700))
    stroke = 2 * line + 1
    font = _font(max(12, round(scale / 70)))
    small = _font(max(10, round(scale / 95)))

    for star in stars:  # landmarks first, so the object stays on top
        sx, sy = view.to_view(star.x, star.y)
        r = max(5, scale / 90)
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), outline=STAR_COLOR, width=line)
        text = star.name or (f"G {star.g_mag:.1f}" if label_stars else None)
        if text:
            _text_block(draw, (sx + r + 2, sy - r), [text], small, STAR_COLOR, width, height, line)

    if outline:
        points = [view.to_view(x, y) for x, y in outline]
        draw.line([*points, points[0]], fill=OBJECT_COLOR, width=stroke, joint="curve")
    ox, oy = view.to_view(obj["projected_x"], obj["projected_y"])
    inner, outer = max(4, round(scale / 60)), max(14, round(scale / 14))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [(ox + dx * inner, oy + dy * inner), (ox + dx * outer, oy + dy * outer)],
            fill=OBJECT_COLOR,
            width=stroke,
        )
    label = object_label(obj, requested_name)
    _text_block(
        draw,
        (ox + outer + 6, oy - outer),
        [label["primary"], label["secondary"]],
        font,
        OBJECT_COLOR,
        width,
        height,
        line,
    )

    margin = max(10, scale // 40)
    bar_px, bar_label = nice_scale(pixel_scale_arcsec / view.zoom, width / 6)
    bx, by = margin, height - margin
    draw.line([(bx, by), (bx + bar_px, by)], fill=TEXT_COLOR, width=stroke)
    draw.text(
        (bx, by - stroke - getattr(small, "size", 10) - 4),
        bar_label,
        fill=TEXT_COLOR,
        font=small,
        stroke_width=line,
        stroke_fill=OUTLINE,
    )

    north, east = orientation(wcs, obj["projected_x"], obj["projected_y"])
    length = max(30, scale / 12)
    cx, cy = (
        width - margin - length - getattr(small, "size", 10),
        height - margin - length - getattr(small, "size", 10),
    )
    for (vx, vy), label in ((north, "N"), (east, "E")):
        tip = (cx + vx * length, cy + vy * length)
        draw.line([(cx, cy), tip], fill=TEXT_COLOR, width=stroke)
        draw.text(
            (
                tip[0] + vx * 6 - getattr(small, "size", 10) / 3,
                tip[1] + vy * 6 - getattr(small, "size", 10) / 2,
            ),
            label,
            fill=TEXT_COLOR,
            font=small,
            stroke_width=line,
            stroke_fill=OUTLINE,
        )

    _text_block(draw, (margin, margin), notes, small, TEXT_COLOR, width, height, line)
    return image


def _text_block(draw, xy, lines, font, color, width, height, line) -> None:
    lines = [t for t in lines if t]
    if not lines:
        return
    text = "\n".join(lines)
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=line)
    x = min(max(xy[0], 0), width - (right - left))
    y = min(max(xy[1], 0), height - (bottom - top))
    draw.multiline_text(
        (x - left, y - top), text, fill=color, font=font, stroke_width=line, stroke_fill=OUTLINE
    )

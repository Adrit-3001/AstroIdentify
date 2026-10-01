"""Object-annotation overlay (coordinate-exact, same dimensions as the image).

Drawn in array orientation (row 0 at the top; no resize, crop or flip), so canonical
pixel coordinates are image pixel coordinates. Pillow addresses pixels by integer index,
the same pixel-centre convention.

* Objects with a catalogued size: the projected sky ellipse (or the conservative circle),
  plus a small centre tick.
* Objects without a size: an open crosshair at the catalogue position (nothing invented).
* Labels: display name and SIMBAD type description, placed beside the marker without
  overlapping earlier labels. Only the first ``max_labels`` objects (listing order) are
  labelled, and only the first ``max_objects`` are drawn.
* A small legend box in the top-left corner (inside the image, so dimensions stay
  unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from astroidentify.config import PreprocessingConfig
from astroidentify.objects.filtering import (
    CATEGORY_GALAXY,
    CATEGORY_GALAXY_GROUP,
    CATEGORY_NEBULA,
    CATEGORY_PLANETARY_NEBULA,
    CATEGORY_STAR_CLUSTER,
    CATEGORY_SUPERNOVA_REMNANT,
)
from astroidentify.objects.types import CatalogObject, IdentificationResult
from astroidentify.preprocessing.preview import stretch_for_display
from astroidentify.types import AstronomyImage

CATEGORY_COLORS: dict[str, tuple[int, int, int]] = {
    CATEGORY_PLANETARY_NEBULA: (0, 230, 255),
    CATEGORY_NEBULA: (255, 120, 200),
    CATEGORY_SUPERNOVA_REMNANT: (255, 90, 90),
    CATEGORY_STAR_CLUSTER: (255, 220, 60),
    CATEGORY_GALAXY: (120, 255, 120),
    CATEGORY_GALAXY_GROUP: (180, 255, 180),
}
DEFAULT_COLOR = (230, 230, 230)
TEXT_OUTLINE = (0, 0, 0)


@dataclass(frozen=True)
class OverlayReport:
    """Which objects were drawn and labelled (``catalogue_id`` values)."""

    drawn: tuple[int, ...]
    labelled: tuple[int, ...]


def render_object_overlay(
    image: AstronomyImage,
    result: IdentificationResult,
    max_objects: int = 40,
    max_labels: int = 25,
) -> tuple[Image.Image, OverlayReport]:
    """Annotated copy of the image; returns the overlay and what was drawn."""
    pixels = stretch_for_display(image, PreprocessingConfig())
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    canvas = Image.fromarray(np.ascontiguousarray(pixels)).convert("RGB")
    width, height = canvas.size
    scale = min(width, height)
    line = max(1, round(scale / 900))
    font = _font(max(11, round(scale / 110)))
    draw = ImageDraw.Draw(canvas)

    retained = list(result.retained)[:max_objects]
    legend_lines = _legend_lines(result, min(len(result.retained), max_objects))
    legend_box = _legend_size(draw, legend_lines, font, line)
    placed: list[tuple[float, float, float, float]] = [(0, 0, *legend_box)]  # keep it clear
    labelled: list[int] = []
    for index, obj in enumerate(retained):
        color = CATEGORY_COLORS.get(obj.category, DEFAULT_COLOR)
        box = _draw_marker(draw, obj, scale, color, line)
        if index < max_labels and _label(draw, obj, box, font, color, width, height, placed, line):
            labelled.append(obj.catalogue_id)

    _draw_legend(canvas, legend_lines, font, line)
    return canvas, OverlayReport(tuple(o.catalogue_id for o in retained), tuple(labelled))


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # Pillow without FreeType: fixed bitmap font
        return ImageFont.load_default()


def _draw_marker(draw, obj: CatalogObject, scale: int, color, line: int):
    """Draw the object's marker; return its bounding box ``(x0, y0, x1, y1)``."""
    x, y = obj.projected_x, obj.projected_y
    # Odd line widths and integer arm lengths keep markers pixel-symmetric about (x, y).
    stroke = 2 * line + 1
    tick = max(4, round(scale / 300))
    if obj.extent.has_size and obj.footprint_px and _span(obj.footprint_px) >= 2 * tick:
        points = [*obj.footprint_px, obj.footprint_px[0]]
        draw.line(points, fill=color, width=stroke, joint="curve")
        if np.isfinite(x) and np.isfinite(y):
            draw.line([(x - tick, y), (x + tick, y)], fill=color, width=stroke)
            draw.line([(x, y - tick), (x, y + tick)], fill=color, width=stroke)
        width, height = draw.im.size
        # Anchor labels on the visible part of the outline (the centre may be off-image).
        visible = [
            (px, py)
            for px, py in obj.footprint_px
            if -0.5 <= px <= width - 0.5 and -0.5 <= py <= height - 0.5
        ] or obj.footprint_px
        xs = [p[0] for p in visible]
        ys = [p[1] for p in visible]
        return min(xs), min(ys), max(xs), max(ys)
    inner, outer = max(3, round(scale / 120)), max(8, round(scale / 45))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line([(x + dx * inner, y + dy * inner), (x + dx * outer, y + dy * outer)],
                  fill=color, width=stroke)  # fmt: skip
    return x - outer, y - outer, x + outer, y + outer


def _span(points) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _label(draw, obj, box, font, color, width, height, placed, line) -> bool:
    text = obj.display_name
    if obj.common_names:
        text += f" ({obj.common_names[0]})"
    text += "\n" + (obj.object_type_description or obj.object_type or obj.category)
    stroke = max(1, line)
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = right - left, bottom - top
    gap = 2 * line + 4
    x0, y0, x1, y1 = box
    candidates = [
        (x1 + gap, y0 - th / 2),  # right
        (x0 - gap - tw, y0 - th / 2),  # left
        ((x0 + x1 - tw) / 2, y1 + gap),  # below
        ((x0 + x1 - tw) / 2, y0 - gap - th),  # above
    ]
    for cx, cy in candidates:
        cx = min(max(cx, 0), width - tw)
        cy = min(max(cy, 0), height - th)
        rect = (cx, cy, cx + tw, cy + th)
        if not any(_overlaps(rect, other) for other in placed):
            draw.multiline_text((cx - left, cy - top), text, fill=color, font=font,
                                stroke_width=stroke, stroke_fill=TEXT_OUTLINE)  # fmt: skip
            placed.append(rect)
            return True
    return False


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _legend_lines(result: IdentificationResult, n_drawn: int) -> list[tuple[str, tuple]]:
    wcs = result.wcs
    wcs_text = "WCS: " + (
        "catalogue-refined" if wcs and wcs.refined else "plate solution" if wcs else "supplied"
    )
    categories = sorted({o.category for o in result.retained})
    return [
        (f"SIMBAD objects ({result.query.origin}); {wcs_text}", DEFAULT_COLOR),
        (f"{len(result.retained)} catalogued in field, {n_drawn} drawn; no confidence implied",
         DEFAULT_COLOR),
        *((c.replace("_", " "), CATEGORY_COLORS.get(c, DEFAULT_COLOR)) for c in categories),
    ]  # fmt: skip


def _legend_metrics(draw, lines, font, line):
    pad = 4 * line + 4
    sizes = [draw.textbbox((0, 0), text, font=font) for text, _ in lines]
    row = max(b[3] - b[1] for b in sizes) + 2 * line + 2
    return pad, sizes, row


def _legend_size(draw, lines, font, line) -> tuple[int, int]:
    pad, sizes, row = _legend_metrics(draw, lines, font, line)
    return max(b[2] - b[0] for b in sizes) + 2 * pad, row * len(lines) + 2 * pad


def _draw_legend(canvas: Image.Image, lines, font, line) -> None:
    """Semi-transparent legend box in the top-left corner."""
    draw = ImageDraw.Draw(canvas)
    pad, sizes, row = _legend_metrics(draw, lines, font, line)
    box_w, box_h = _legend_size(draw, lines, font, line)
    base = canvas.convert("RGBA")
    base.alpha_composite(Image.new("RGBA", (box_w, box_h), (0, 0, 0, 170)), (0, 0))
    canvas.paste(base.convert("RGB"))
    draw = ImageDraw.Draw(canvas)
    for i, ((text, color), bbox) in enumerate(zip(lines, sizes, strict=True)):
        draw.text((pad - bbox[0], pad + i * row - bbox[1]), text, fill=color, font=font)

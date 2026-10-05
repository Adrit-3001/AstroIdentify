"""Evidence overlay: retained objects coloured by support level (coordinate-exact).

Same conventions as the Milestone 5 overlay: array orientation, no resize/crop/flip, the
image's own dimensions, and an in-image legend. Objects are drawn strongest support first,
up to ``max_objects``. Labels show the display name and support level (never a percentage).
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from astroidentify.config import PreprocessingConfig
from astroidentify.evidence.types import (
    LEVEL_CATALOGUE_ONLY,
    LEVEL_INSUFFICIENT,
    LEVEL_MODERATE,
    LEVEL_STRONG,
    LEVEL_WEAK,
    SUPPORT_LEVELS,
    EvidenceResult,
    ObjectEvidence,
)
from astroidentify.objects.overlay import _draw_legend, _font, _legend_size, _overlaps
from astroidentify.preprocessing.preview import stretch_for_display
from astroidentify.types import AstronomyImage

LEVEL_COLORS: dict[str, tuple[int, int, int]] = {
    LEVEL_STRONG: (60, 255, 60),
    LEVEL_MODERATE: (200, 255, 60),
    LEVEL_WEAK: (255, 170, 40),
    LEVEL_CATALOGUE_ONLY: (130, 170, 255),
    LEVEL_INSUFFICIENT: (170, 170, 170),
}
TEXT_OUTLINE = (0, 0, 0)


def drawing_order(result: EvidenceResult) -> list[ObjectEvidence]:
    """Strongest support first; ties keep the Milestone 5 listing order."""
    indexed = list(enumerate(result.objects))
    indexed.sort(key=lambda item: (SUPPORT_LEVELS.index(item[1].support_level), item[0]))
    return [o for _, o in indexed]


def render_evidence_overlay(
    image: AstronomyImage,
    result: EvidenceResult,
    footprints: dict[int, list[tuple[float, float]]],
    max_objects: int = 40,
    max_labels: int = 25,
) -> tuple[Image.Image, tuple[int, ...]]:
    """Annotated copy of the image and the ``catalogue_id`` values drawn."""
    pixels = stretch_for_display(image, PreprocessingConfig())
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    canvas = Image.fromarray(np.ascontiguousarray(pixels)).convert("RGB")
    width, height = canvas.size
    scale = min(width, height)
    line = max(1, round(scale / 900))
    stroke = 2 * line + 1  # odd: symmetric about the position
    font = _font(max(11, round(scale / 110)))
    draw = ImageDraw.Draw(canvas)

    drawn = drawing_order(result)[:max_objects]
    counts = result.counts()
    legend = [
        ("Evidence support levels (not probabilities)", (230, 230, 230)),
        *((f"{level}: {counts[level]}", LEVEL_COLORS[level]) for level in SUPPORT_LEVELS),
    ]
    placed = [(0, 0, *_legend_size(draw, legend, font, line))]
    for index, obj in enumerate(drawn):
        color = LEVEL_COLORS[obj.support_level]
        box = _marker(
            draw, obj, footprints.get(obj.catalogue_id), scale, color, stroke, width, height
        )
        if index < max_labels:
            _label(
                draw,
                f"{obj.display_name}\n{obj.support_level}",
                box,
                font,
                color,
                width,
                height,
                placed,
                line,
            )
    _draw_legend(canvas, legend, font, line)
    return canvas, tuple(o.catalogue_id for o in drawn)


def _marker(draw, obj, footprint, scale, color, stroke, width, height):
    x, y = obj.projected_x, obj.projected_y
    if footprint and len(footprint) > 2:
        xs = [p[0] for p in footprint]
        ys = [p[1] for p in footprint]
        if max(max(xs) - min(xs), max(ys) - min(ys)) >= scale / 60:
            draw.line([*footprint, footprint[0]], fill=color, width=stroke, joint="curve")
            visible = [
                (px, py)
                for px, py in footprint
                if -0.5 <= px <= width - 0.5 and -0.5 <= py <= height - 0.5
            ] or footprint
            return (
                min(p[0] for p in visible),
                min(p[1] for p in visible),
                max(p[0] for p in visible),
                max(p[1] for p in visible),
            )
    inner, outer = max(3, round(scale / 120)), max(8, round(scale / 45))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [(x + dx * inner, y + dy * inner), (x + dx * outer, y + dy * outer)],
            fill=color,
            width=stroke,
        )
    return x - outer, y - outer, x + outer, y + outer


def _label(draw, text, box, font, color, width, height, placed, line) -> bool:
    stroke = max(1, line)
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = right - left, bottom - top
    gap = 2 * line + 4
    x0, y0, x1, y1 = box
    for cx, cy in (
        (x1 + gap, y0 - th / 2),
        (x0 - gap - tw, y0 - th / 2),
        ((x0 + x1 - tw) / 2, y1 + gap),
        ((x0 + x1 - tw) / 2, y0 - gap - th),
    ):
        cx, cy = min(max(cx, 0), width - tw), min(max(cy, 0), height - th)
        rect = (cx, cy, cx + tw, cy + th)
        if not any(_overlaps(rect, other) for other in placed):
            draw.multiline_text(
                (cx - left, cy - top),
                text,
                fill=color,
                font=font,
                stroke_width=stroke,
                stroke_fill=TEXT_OUTLINE,
            )
            placed.append(rect)
            return True
    return False

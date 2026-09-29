"""Diagnostic overlay and background-map visualizations (display only).

The overlay is drawn at full resolution in array orientation (row 0 at the top) on the
Milestone 1 display stretch, so marker positions equal the exported ``x``/``y`` exactly.
There is no resizing, cropping or flipping. The legend is a strip appended *below* the
image (rows ``H`` onward), so it never hides sources and image pixel ``(x, y)`` is overlay
pixel ``(x, y)``.

Markers:

* accepted: green circle with the photometry aperture radius (cyan if edge-flagged);
* rejected: small red circle;
* saturated: an extra yellow ring outside the aperture (accepted or not);
* labels: IDs of the ``overlay_max_labels`` brightest accepted sources.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from astroidentify.detection.background import map_statistics
from astroidentify.detection.types import DetectionResult
from astroidentify.preprocessing.normalize import robust_range
from astroidentify.preprocessing.preview import stretch_for_display

ACCEPTED_COLOR = (0, 255, 0)
EDGE_COLOR = (0, 220, 255)
REJECTED_COLOR = (255, 40, 40)
SATURATED_COLOR = (255, 230, 0)
LABEL_COLOR = (255, 255, 255)

# Markers get thicker on large images so they stay visible when zoomed out.
_PIXELS_PER_LINE_WIDTH = 1200
_REJECTED_RADIUS_FWHM = 0.6
_SATURATED_RING_GAP = 3
_LEGEND_ROW_HEIGHT = 12


def render_overlay(result: DetectionResult) -> Image.Image:
    """Draw accepted/rejected/saturated/edge markers on the display-stretched image."""
    pixels = stretch_for_display(result.preprocessing.image, result.preprocessing.config)
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    height, width = result.plane.shape
    legend = _legend_lines(result)
    canvas = Image.new("RGB", (width, height + _LEGEND_ROW_HEIGHT * len(legend) + 4))
    canvas.paste(Image.fromarray(np.ascontiguousarray(pixels)), (0, 0))
    draw = ImageDraw.Draw(canvas)

    line = max(1, round(min(height, width) / _PIXELS_PER_LINE_WIDTH))
    radius = result.aperture_radius
    small = max(3.0, _REJECTED_RADIUS_FWHM * result.fwhm.value)

    # Rejected first so accepted markers are drawn on top.
    for source in result.rejected_sources:
        _circle(draw, source.x, source.y, small, REJECTED_COLOR, line)
    for source in result.accepted_sources:
        color = EDGE_COLOR if source.edge else ACCEPTED_COLOR
        _circle(draw, source.x, source.y, radius, color, line)
    for source in result.sources:
        if source.saturated:
            _circle(
                draw, source.x, source.y, radius + _SATURATED_RING_GAP + line, SATURATED_COLOR, line
            )

    font = ImageFont.load_default()
    for source in result.brightest(result.config.overlay_max_labels):
        draw.text(
            (source.x + radius + 2, source.y - radius),
            str(source.source_id),
            fill=LABEL_COLOR,
            font=font,
        )

    _draw_legend(draw, legend, height, font)
    return canvas


def render_map(array: np.ndarray) -> Image.Image:
    """Grayscale visualization of a background or RMS map (0.5-99.5 percentile stretch)."""
    low, high, _ = robust_range(array, 0.5, 99.5)
    scaled = np.clip((array - low) / (high - low), 0.0, 1.0)
    return Image.fromarray(np.round(scaled * 255).astype(np.uint8))


def _circle(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int],
    width: int,
) -> None:
    # Pillow addresses pixels by integer index, the same pixel-centre convention as x/y.
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)


def _legend_lines(result: DetectionResult) -> list[tuple[str, tuple[int, int, int]]]:
    diagnostics = result.diagnostics
    rms = map_statistics(result.background.rms)
    return [
        (
            f"accepted {diagnostics['n_accepted']} (cyan = edge-flagged, "
            f"{diagnostics['n_edge_flagged_accepted']})",
            ACCEPTED_COLOR,
        ),
        (f"rejected {diagnostics['n_rejected']} (small red circles)", REJECTED_COLOR),
        (f"saturated {diagnostics['n_saturated']} (yellow outer ring)", SATURATED_COLOR),
        (
            f"FWHM {result.fwhm.value:.2f} px ({result.fwhm.method}); "
            f"median background RMS {rms['median']:.3g}",
            LABEL_COLOR,
        ),
    ]


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    lines: list[tuple[str, tuple[int, int, int]]],
    top: int,
    font: ImageFont.ImageFont,
) -> None:
    for i, (text, color) in enumerate(lines):
        draw.text((4, top + 2 + _LEGEND_ROW_HEIGHT * i), text, fill=color, font=font)

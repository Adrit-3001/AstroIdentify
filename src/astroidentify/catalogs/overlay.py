"""Catalogue-match diagnostic overlay (display only, coordinate-exact).

Drawn at full resolution in array orientation (row 0 at the top, no resize/crop/flip); the
legend is a strip appended below the image. Markers:

* grey ring: accepted detection without a catalogue match;
* cyan dot: projected position of an eligible (brightness-limited) catalogue star;
* green ring: one-to-one match (ring at the observed detection);
* orange line: residual vector from predicted to observed position, magnified
  ``RESIDUAL_MAGNIFICATION`` times so sub-pixel systematics are visible;
* labels: Gaia G magnitude of the brightest matched stars (no object names).
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from astroidentify.catalogs.types import CatalogMatchResult
from astroidentify.config import PreprocessingConfig
from astroidentify.preprocessing.preview import stretch_for_display
from astroidentify.types import AstronomyImage

UNMATCHED_COLOR = (150, 150, 150)
CATALOG_COLOR = (0, 210, 255)
MATCH_COLOR = (0, 255, 0)
RESIDUAL_COLOR = (255, 150, 0)
TEXT_COLOR = (255, 255, 255)
RESIDUAL_MAGNIFICATION = 20.0

_LEGEND_ROW_HEIGHT = 12
_PIXELS_PER_LINE_WIDTH = 1200


def render_catalog_overlay(
    image: AstronomyImage, result: CatalogMatchResult, max_labels: int = 20
) -> Image.Image:
    """Overlay of detections, projected catalogue stars, matches and residual vectors."""
    pixels = stretch_for_display(image, PreprocessingConfig())
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    legend = _legend(result)
    height, width = image.height, image.width
    canvas = Image.new("RGB", (width, height + _LEGEND_ROW_HEIGHT * len(legend) + 4))
    canvas.paste(Image.fromarray(np.ascontiguousarray(pixels)), (0, 0))
    draw = ImageDraw.Draw(canvas)
    line = max(1, round(min(height, width) / _PIXELS_PER_LINE_WIDTH))
    ring = max(6.0, 2.5 * result.match_radius_px)

    projected = result.projected
    for k in np.flatnonzero(result.eligible_catalog_mask & projected.in_image):
        _dot(draw, projected.x[k], projected.y[k], 1.5 + line, CATALOG_COLOR)
    unmatched = set(result.unmatched_detection_ids)
    for source in result.detections:
        if source.source_id in unmatched:
            _circle(draw, source.x, source.y, ring, UNMATCHED_COLOR, line)
    for match in result.matches:
        _circle(draw, match.observed_x_px, match.observed_y_px, ring, MATCH_COLOR, line)
        dx = (match.observed_x_px - match.predicted_x_px) * RESIDUAL_MAGNIFICATION
        dy = (match.observed_y_px - match.predicted_y_px) * RESIDUAL_MAGNIFICATION
        start = (match.observed_x_px, match.observed_y_px)
        draw.line([start, (start[0] + dx, start[1] + dy)], fill=RESIDUAL_COLOR, width=line + 1)

    font = ImageFont.load_default()
    brightest = sorted(
        (m for m in result.matches if np.isfinite(m.phot_g_mean_mag)),
        key=lambda m: m.phot_g_mean_mag,
    )[:max_labels]
    for match in brightest:
        draw.text(
            (match.observed_x_px + ring + 2, match.observed_y_px - ring),
            f"G{match.phot_g_mean_mag:.1f}",
            fill=TEXT_COLOR,
            font=font,
        )
    for i, (text, color) in enumerate(legend):
        draw.text((4, height + 2 + _LEGEND_ROW_HEIGHT * i), text, fill=color, font=font)
    return canvas


def _legend(result: CatalogMatchResult) -> list[tuple[str, tuple[int, int, int]]]:
    s = result.summary
    median = f'{s.median_residual_arcsec:.2f}"' if s.median_residual_arcsec is not None else "n/a"
    wcs_state = "refined against catalogue" if result.wcs_refined else "as input"
    return [
        (
            f"{s.catalog}: {s.rows_returned} rows, {s.rows_in_image} in image, "
            f"{s.rows_eligible} eligible (cyan dots)",
            CATALOG_COLOR,
        ),
        (
            f"{s.matches} one-to-one matches of {s.detections_considered} accepted detections "
            f'(green), median residual {median}, radius {result.match_radius_arcsec:g}"',
            MATCH_COLOR,
        ),
        (
            f"orange: residual vectors x{RESIDUAL_MAGNIFICATION:g}; grey: {s.unmatched_detections} "
            f"unmatched detections; WCS {wcs_state}",
            RESIDUAL_COLOR,
        ),
    ]


def _circle(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, color, width: int) -> None:
    # Pillow addresses pixels by integer index: the same pixel-centre convention as x/y.
    draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=width)


def _dot(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, color) -> None:
    draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

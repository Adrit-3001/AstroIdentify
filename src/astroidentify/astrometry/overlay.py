"""Plate-solving diagnostic images (display only).

Both overlays are drawn at full resolution in array orientation (row 0 at the top, no
resize/crop/flip), so every marker sits exactly at its canonical pixel coordinates. The
legend is a strip appended below the image, as in the detection overlay.
"""

from __future__ import annotations

import math

import numpy as np
from astropy.coordinates import SkyCoord
from PIL import Image, ImageDraw, ImageFont

from astroidentify.astrometry.types import (
    TIER_EDGE,
    TIER_PREFERRED,
    TIER_SATURATED,
    TIER_SECONDARY,
    PlateSolution,
    SourceSelection,
)
from astroidentify.astrometry.wcs import format_dec, format_ra, pixel_to_sky, sky_to_pixel
from astroidentify.detection.types import DetectionResult
from astroidentify.preprocessing.preview import stretch_for_display

TIER_COLORS = {
    TIER_PREFERRED: (0, 255, 0),
    TIER_SECONDARY: (0, 220, 255),
    TIER_SATURATED: (255, 230, 0),
    TIER_EDGE: (255, 140, 0),
}
DETECTION_COLOR = (120, 120, 120)
GRID_COLOR = (90, 150, 255)
BALANCE_GRID_COLOR = (70, 70, 140)
CENTRE_COLOR = (255, 60, 200)
MATCH_COLOR = (0, 255, 0)
TEXT_COLOR = (255, 255, 255)

_LEGEND_ROW_HEIGHT = 12
_PIXELS_PER_LINE_WIDTH = 1200
# Nice RA/Dec grid steps in degrees (1 arcsec .. 90 deg).
_GRID_STEPS_DEG = (
    *(v / 3600 for v in (1, 2, 5, 10, 15, 30)),
    *(v / 60 for v in (1, 2, 5, 10, 15, 20, 30)),
    1, 2, 5, 10, 15, 20, 30, 45, 90,
)  # fmt: skip
_TARGET_GRID_LINES = 5
_GRID_SAMPLES = 400


def render_selection_overlay(
    detection: DetectionResult, selection: SourceSelection, max_labels: int = 30
) -> Image.Image:
    """All accepted detections (grey), selected sources coloured by tier, balancing grid."""
    canvas, draw, line, font = _canvas(detection, _selection_legend(selection))
    width, height = selection.image_width, selection.image_height

    columns, rows = selection.grid_shape
    for c in range(1, columns):
        x = c * width / columns - 0.5
        draw.line([(x, 0), (x, height - 1)], fill=BALANCE_GRID_COLOR, width=line)
    for r in range(1, rows):
        y = r * height / rows - 0.5
        draw.line([(0, y), (width - 1, y)], fill=BALANCE_GRID_COLOR, width=line)

    radius = max(6.0, 0.8 * detection.aperture_radius)
    for source in detection.accepted_sources:
        _circle(draw, source.x, source.y, radius * 0.6, DETECTION_COLOR, line)
    for selected in selection.sources:
        _circle(draw, selected.x, selected.y, radius, TIER_COLORS[selected.tier], line + 1)
    for selected in sorted(selection.sources, key=lambda s: s.rank)[:max_labels]:
        draw.text(
            (selected.x + radius + 2, selected.y - radius),
            str(selected.rank),
            fill=TEXT_COLOR,
            font=font,
        )
    _draw_legend(draw, _selection_legend(selection), height, font)
    return canvas


def render_wcs_overlay(detection: DetectionResult, solution: PlateSolution) -> Image.Image:
    """RA/Dec grid, image-centre marker, selected and matched stars."""
    if not solution.solved or solution.wcs is None or solution.geometry is None:
        raise ValueError("a WCS overlay needs a solved plate solution")
    legend = _wcs_legend(solution)
    canvas, draw, line, font = _canvas(detection, legend)
    height, width = detection.plane.shape
    wcs = solution.wcs

    for points, label in grid_lines(wcs, width, height):
        _polyline(draw, points, width, height, GRID_COLOR, line)
        anchor = _label_anchor(points, width, height)
        if anchor is not None:
            draw.text(anchor, label, fill=GRID_COLOR, font=font)

    radius = max(6.0, 0.8 * detection.aperture_radius)
    if solution.selection is not None:
        for source in solution.selection.sources:
            _circle(draw, source.x, source.y, radius, TEXT_COLOR, line)
    for match in solution.correspondences:
        _circle(draw, match.field_x, match.field_y, radius + 3 + line, MATCH_COLOR, line)

    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    arm = max(12.0, min(width, height) / 40)
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=CENTRE_COLOR, width=line + 1)
    draw.line([(cx, cy - arm), (cx, cy + arm)], fill=CENTRE_COLOR, width=line + 1)
    centre = solution.geometry.centre
    draw.text(
        (cx + arm + 4, cy + 4),
        f"centre {format_ra(centre.ra_deg)} {format_dec(centre.dec_deg)}",
        fill=CENTRE_COLOR,
        font=font,
    )
    _draw_legend(draw, legend, height, font)
    return canvas


def grid_lines(wcs, width: int, height: int) -> list[tuple[np.ndarray, str]]:
    """Constant-Dec and constant-RA curves (canonical pixel points) crossing the image."""
    border = np.concatenate(
        [
            np.column_stack([np.linspace(-0.5, width - 0.5, 50), np.full(50, -0.5)]),
            np.column_stack([np.linspace(-0.5, width - 0.5, 50), np.full(50, height - 0.5)]),
            np.column_stack([np.full(50, -0.5), np.linspace(-0.5, height - 0.5, 50)]),
            np.column_stack([np.full(50, width - 0.5), np.linspace(-0.5, height - 0.5, 50)]),
            [[(width - 1) / 2, (height - 1) / 2]],
        ]
    )
    ra, dec = pixel_to_sky(wcs, border[:, 0], border[:, 1])
    ra0 = float(ra[-1])
    dra = (ra - ra0 + 180.0) % 360.0 - 180.0  # unwrap around the centre
    pole_inside = _pole_in_image(wcs, width, height)
    if pole_inside:
        dra_min, dra_max = -180.0, 180.0
    else:
        dra_min, dra_max = float(dra.min()), float(dra.max())
    dec_min, dec_max = float(dec.min()), float(dec.max())
    if pole_inside:
        dec_max = 90.0 if dec_max > 0 else dec_max
        dec_min = -90.0 if dec_min < 0 else dec_min

    lines: list[tuple[np.ndarray, str]] = []
    dec_step = _nice_step(dec_max - dec_min)
    ra_step = _nice_step(dra_max - dra_min)
    ra_samples = np.linspace(dra_min - ra_step, dra_max + ra_step, _GRID_SAMPLES) + ra0
    dec_samples = np.linspace(
        max(-90, dec_min - dec_step), min(90, dec_max + dec_step), _GRID_SAMPLES
    )

    for value in _multiples(dec_min, dec_max, dec_step):
        x, y = sky_to_pixel(
            wcs, ra_samples % 360.0, np.full(_GRID_SAMPLES, value), best_effort=True
        )
        lines.append((np.column_stack([x, y]), f"Dec {format_dec(value)}"))
    for value in _multiples(ra0 + dra_min, ra0 + dra_max, ra_step):
        x, y = sky_to_pixel(
            wcs, np.full(_GRID_SAMPLES, value % 360.0), dec_samples, best_effort=True
        )
        lines.append((np.column_stack([x, y]), f"RA {format_ra(value % 360.0)}"))
    return lines


def _pole_in_image(wcs, width: int, height: int) -> bool:
    """Conservatively: is a celestial pole within the field's circumscribed circle?"""
    ra, dec = pixel_to_sky(
        wcs,
        np.array([(width - 1) / 2, -0.5, width - 0.5, -0.5, width - 0.5]),
        np.array([(height - 1) / 2, -0.5, -0.5, height - 0.5, height - 0.5]),
    )
    centre = SkyCoord(ra[0], dec[0], unit="deg")
    radius = max(
        centre.separation(SkyCoord(r, d, unit="deg")).deg
        for r, d in zip(ra[1:], dec[1:], strict=True)
    )
    return 90.0 - abs(float(dec[0])) <= radius


def _nice_step(span_deg: float) -> float:
    target = max(span_deg, 1e-6) / _TARGET_GRID_LINES
    for step in _GRID_STEPS_DEG:
        if step >= target:
            return float(step)
    return float(_GRID_STEPS_DEG[-1])


def _multiples(low: float, high: float, step: float) -> list[float]:
    first = math.ceil(low / step)
    last = math.floor(high / step)
    return [k * step for k in range(first, last + 1)]


def _polyline(draw, points: np.ndarray, width: int, height: int, color, line: int) -> None:
    margin = max(width, height)
    segment: list[tuple[float, float]] = []
    for x, y in points:
        ok = (
            np.isfinite(x)
            and np.isfinite(y)
            and -margin < x < width + margin
            and -margin < y < height + margin
        )
        if ok:
            segment.append((float(x), float(y)))
        elif len(segment) > 1:
            draw.line(segment, fill=color, width=line)
            segment = []
        else:
            segment = []
    if len(segment) > 1:
        draw.line(segment, fill=color, width=line)


def _label_anchor(points: np.ndarray, width: int, height: int) -> tuple[float, float] | None:
    inside = [
        (x, y)
        for x, y in points
        if np.isfinite(x) and np.isfinite(y) and 2 <= x < width - 120 and 2 <= y < height - 14
    ]
    return (inside[0][0] + 3, inside[0][1] + 2) if inside else None


def _canvas(detection: DetectionResult, legend: list[tuple[str, tuple[int, int, int]]]):
    pixels = stretch_for_display(detection.preprocessing.image, detection.preprocessing.config)
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    height, width = detection.plane.shape
    canvas = Image.new("RGB", (width, height + _LEGEND_ROW_HEIGHT * len(legend) + 4))
    canvas.paste(Image.fromarray(np.ascontiguousarray(pixels)), (0, 0))
    line = max(1, round(min(height, width) / _PIXELS_PER_LINE_WIDTH))
    return canvas, ImageDraw.Draw(canvas), line, ImageFont.load_default()


def _circle(draw, x: float, y: float, radius: float, color, width: int) -> None:
    # Pillow addresses pixels by integer index: the same pixel-centre convention as x/y.
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)


def _draw_legend(draw, lines, top: int, font) -> None:
    for i, (text, color) in enumerate(lines):
        draw.text((4, top + 2 + _LEGEND_ROW_HEIGHT * i), text, fill=color, font=font)


def _selection_legend(selection: SourceSelection) -> list[tuple[str, tuple[int, int, int]]]:
    by_tier = selection.n_by_tier
    columns, rows = selection.grid_shape
    n_accepted = sum(selection.n_candidates_by_tier.values())
    lines = [
        (
            f"selected {len(selection)} of {n_accepted} accepted; "
            f"grid {columns}x{rows}, {selection.occupied_cells} cells used (grey = not selected)",
            TEXT_COLOR,
        )
    ]
    for tier in (TIER_PREFERRED, TIER_SECONDARY, TIER_SATURATED, TIER_EDGE):
        lines.append((f"{tier}: {by_tier.get(tier, 0)}", TIER_COLORS[tier]))
    return lines


def _wcs_legend(solution: PlateSolution) -> list[tuple[str, tuple[int, int, int]]]:
    geometry = solution.geometry
    assert geometry is not None
    stats = solution.match_statistics
    centre = geometry.centre
    lines = [
        (
            f"centre RA {centre.ra_deg:.5f} ({format_ra(centre.ra_deg)})  "
            f"Dec {centre.dec_deg:+.5f} ({format_dec(centre.dec_deg)})",
            CENTRE_COLOR,
        ),
        (
            f'scale {geometry.pixel_scale_arcsec:.3f}"/px  field '
            f"{geometry.field_width_deg * 60:.2f}' x {geometry.field_height_deg * 60:.2f}'  "
            f"up PA {geometry.up_position_angle_deg:.2f} deg E of N  parity {geometry.parity}",
            TEXT_COLOR,
        ),
        (f"mode {solution.mode}; RA/Dec grid in blue; white = selected sources", GRID_COLOR),
    ]
    if stats is not None:
        residual = (
            f'; median residual {stats.median_residual_arcsec:.2f}"'
            if stats.median_residual_arcsec is not None
            else ""
        )
        lines.append(
            (f"green rings = {stats.n_matched} solver-matched stars{residual}", MATCH_COLOR)
        )
    return lines

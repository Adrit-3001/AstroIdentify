"""Saturation handling: saturation level, saturated pixels and saturated-core consolidation.

A saturated star has a flat, clipped core. DAOFIND can find several local maxima on the rim
of a large plateau, and its marginal-Gaussian centroids are biased there. Candidates that
sit on the same connected saturated region (within ``SATURATED_CORE_MATCH_FWHM`` * FWHM of
it) are therefore consolidated: the brightest becomes the primary, positioned at the
centroid of the saturated region (the centre of the clipped plateau); the others are
marked as duplicates of it and later rejected with a reason. The region's axis ratio is
recorded because a saturated *stellar* core is compact even when the star's wings are
distorted, whereas saturated extended structure (e.g. bright nebular arcs) is elongated.
Saturated sources are never rejected for saturation alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from astroidentify.config import DetectionConfig
from astroidentify.types import AstronomyImage

# A candidate belongs to a saturated region if its centroid is within this many FWHM of the
# nearest pixel of that region (i.e. it sits on the core rather than merely nearby).
SATURATED_CORE_MATCH_FWHM = 0.5

CENTROID_SATURATED_CORE = "saturated_core"


def resolve_saturation_level(
    image: AstronomyImage, config: DetectionConfig
) -> tuple[float | None, str]:
    """Return ``(level, provenance)``; ``level`` is ``None`` if it cannot be determined."""
    if config.saturation_level is not None:
        return float(config.saturation_level), "config"
    raster = image.metadata.get("raster")
    if raster and raster.get("nominal_max") is not None:
        return float(raster["nominal_max"]), "raster_nominal_max"
    if image.header is not None and "SATURATE" in image.header:
        try:
            return float(image.header["SATURATE"]), "fits_saturate_keyword"
        except (TypeError, ValueError):
            pass
    return None, "unknown"


def saturated_pixel_mask(image: AstronomyImage, level: float | None) -> np.ndarray:
    """Bool ``(H, W)``: ``True`` where any channel is at or above ``level``."""
    if level is None:
        return np.zeros((image.height, image.width), dtype=bool)
    with np.errstate(invalid="ignore"):
        at_level = image.data >= level
    return at_level.any(axis=2) if at_level.ndim == 3 else at_level


@dataclass(frozen=True)
class CoreAssignment:
    """Result of consolidating candidates onto saturated cores (arrays per candidate).

    Attributes:
        xy: Positions to measure at (saturated-core centroids for primaries).
        centroid_method: The detector's centroid method, or ``CENTROID_SATURATED_CORE`` for
            primaries repositioned onto their saturated core.
        primary_index: Index of the primary candidate of the same core for duplicates, else -1.
        core_axis_ratio: Minor/major axis ratio of the saturated region a candidate sits
            on (from second moments), NaN if it is not on a saturated core.
        core_area: Pixel count of that saturated region, 0 if not on a saturated core.
    """

    xy: np.ndarray
    centroid_method: list[str]
    primary_index: np.ndarray
    core_axis_ratio: np.ndarray
    core_area: np.ndarray


def consolidate_saturated_cores(
    xy: np.ndarray,
    flux: np.ndarray,
    methods: list[str],
    saturated_pixels: np.ndarray,
    fwhm: float,
) -> CoreAssignment:
    """Group candidates by saturated region and pick one primary per region."""
    n = len(xy)
    positions = xy.astype(float).copy()
    methods = list(methods)
    primary_index = np.full(n, -1, dtype=int)
    axis_ratio = np.full(n, np.nan)
    area = np.zeros(n, dtype=int)
    if n == 0 or not saturated_pixels.any():
        return CoreAssignment(positions, methods, primary_index, axis_ratio, area)

    labels, _ = ndimage.label(saturated_pixels, structure=np.ones((3, 3), dtype=bool))
    # Distance from every pixel to the nearest saturated pixel, and that pixel's location.
    distance, (near_rows, near_cols) = ndimage.distance_transform_edt(
        ~saturated_pixels, return_indices=True
    )
    rows = np.clip(np.rint(xy[:, 1]).astype(int), 0, saturated_pixels.shape[0] - 1)
    cols = np.clip(np.rint(xy[:, 0]).astype(int), 0, saturated_pixels.shape[1] - 1)
    on_core = distance[rows, cols] <= SATURATED_CORE_MATCH_FWHM * fwhm
    region = np.where(on_core, labels[near_rows[rows, cols], near_cols[rows, cols]], 0)

    slices = ndimage.find_objects(labels)
    for label in np.unique(region[region > 0]):
        members = np.flatnonzero(region == label)
        primary = members[np.argmax(np.nan_to_num(flux[members], nan=-np.inf))]
        rows_px, cols_px = np.nonzero(labels[slices[label - 1]] == label)
        rows_px = rows_px + slices[label - 1][0].start
        cols_px = cols_px + slices[label - 1][1].start
        positions[primary] = (cols_px.mean(), rows_px.mean())
        methods[primary] = CENTROID_SATURATED_CORE
        primary_index[members[members != primary]] = primary
        axis_ratio[members] = _axis_ratio(cols_px, rows_px)
        area[members] = len(cols_px)
    return CoreAssignment(positions, methods, primary_index, axis_ratio, area)


def _axis_ratio(cols: np.ndarray, rows: np.ndarray) -> float:
    """Minor/major axis ratio of a pixel set from its second moments.

    Each pixel is treated as a uniform unit square (variance 1/12 per axis), so a single
    pixel or a compact blob comes out round (ratio 1).
    """
    covariance = np.eye(2) / 12.0
    if len(cols) > 1:
        covariance = covariance + np.cov(np.vstack([cols, rows]), bias=True)
    low, high = np.linalg.eigvalsh(covariance)
    return float(np.sqrt(low / high))

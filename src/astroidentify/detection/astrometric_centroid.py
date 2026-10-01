"""Astrometric centroids: isophote-calibrated positions for saturated stars.

Why
---
A saturated star's clipped plateau is the PSF's *isophote at the saturation level*. The
plateau centroid (``centroid_method="saturated_core"``) equals the star's position only for a
point-symmetric PSF. For an asymmetric PSF (coma, tracking or optical tails, as in many
consumer/processed images) an isophote's centroid moves toward the asymmetric side as the
isophote grows, so the brighter the star, the larger the core and the larger the bias.

How
---
The bias is measured on the image's *own* PSF, with image data only:

1. stack bright, isolated, unsaturated, non-edge DAOFIND stars, each aligned on its DAOFIND
   centroid (the astrometric reference used for every unsaturated source) and normalised by
   flux; take the per-pixel median;
2. for a ladder of isophote levels (``ISOPHOTE_LEVELS`` x peak), record the area and the
   centroid offset of the connected isophote region around the peak, stopping once a region
   touches the stack border;
3. for a saturated star with a plateau of ``A`` pixels, the astrometric centroid is the
   plateau centroid minus the offset interpolated at area ``A``.

The correction is *anchored* to the smallest isophote: the offset table is taken relative to
the offset of the peak region, so a tiny saturated core (essentially a clipped peak) gets no
correction. The plateau is defined on the raw pixel values, the stack on the detection
plane; area-matching is invariant to any monotonic tone curve, which is why this works on
stretched raster images.

Fallback: if no calibration can be built (too few suitable stars) or a plateau is larger than
the largest calibrated isophote, the plateau centroid is kept unchanged and the source is
labelled ``ASTROMETRIC_FALLBACK``. No extrapolation is attempted.

Evaluation (Milestone 4.1, offline): on the Unistellar benchmark the correction reduced the
held-out saturated-star residual against Gaia DR3 from median 1.17 / RMS 2.73 px to
0.82 / 1.34 px, without changing unsaturated sources. Gaia is *not* used here.

Only ``Source.astrometric_x``/``astrometric_y`` change; the detection centroid ``x``/``y``,
IDs, photometry and filtering are untouched.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from astroidentify.config import DetectionConfig
from astroidentify.detection.saturation import CENTROID_SATURATED_CORE
from astroidentify.detection.types import Source, brightness_key

logger = logging.getLogger(__name__)

#: ``astrometric_method`` values.
ASTROMETRIC_DETECTION = "detection"  # same as the detection centroid
ASTROMETRIC_ISOPHOTE = "isophote_calibrated"  # saturated core, corrected
ASTROMETRIC_FALLBACK = "saturated_core_fallback"  # saturated core, correction unavailable

#: Isophote levels as fractions of the stacked PSF peak (geometric ladder, bright -> faint).
ISOPHOTE_LEVELS = np.geomspace(0.95, 0.01, 80)


@dataclass(frozen=True, eq=False)
class IsophoteCalibration:
    """Isophote-centroid offset of the image's stacked PSF as a function of isophote area.

    Attributes:
        area: Isophote areas in pixels, increasing.
        dx, dy: Isophote centroid minus stacked-PSF centre (DAOFIND convention), pixels.
        n_stars: Stars in the stack.
        stack_half_width: Half-size of the stack cutout, pixels.
    """

    area: np.ndarray
    dx: np.ndarray
    dy: np.ndarray
    n_stars: int
    stack_half_width: int

    @property
    def max_area(self) -> float:
        return float(self.area[-1])

    def correction(self, core_area: float) -> tuple[float, float] | None:
        """Offset ``(dx, dy)`` to subtract from a plateau centroid of ``core_area`` pixels.

        ``None`` if the area is outside the calibrated range (no extrapolation). Areas below
        the smallest isophote get no correction (the anchor).
        """
        if not 0 < core_area <= self.max_area:
            return None
        dx = float(np.interp(core_area, self.area, self.dx)) - float(self.dx[0])
        dy = float(np.interp(core_area, self.area, self.dy)) - float(self.dy[0])
        return dx, dy

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_stars": self.n_stars,
            "stack_half_width_px": self.stack_half_width,
            "anchor_offset_px": [float(self.dx[0]), float(self.dy[0])],
            "max_area_px": self.max_area,
            "area_px": [float(a) for a in self.area],
            "offset_dx_px": [float(v) for v in self.dx],
            "offset_dy_px": [float(v) for v in self.dy],
        }


def build_isophote_calibration(
    sources: Iterable[Source],
    subtracted: np.ndarray,
    saturated_pixels: np.ndarray,
    fwhm: float,
    config: DetectionConfig,
) -> tuple[IsophoteCalibration | None, str]:
    """Measure the isophote offset table from the image's own unsaturated stars.

    Returns ``(calibration, note)``; ``calibration`` is ``None`` (with the reason in
    ``note``) when there are too few suitable stars or too few isophote levels.
    """
    half = math.ceil(config.isophote_stack_half_width_fwhm * fwhm)
    height, width = subtracted.shape
    accepted = [s for s in sources if s.accepted]
    if not accepted:
        return None, "no accepted sources"
    xy = np.array([(s.x, s.y) for s in accepted], float)
    pool = sorted(
        (
            s for s in accepted
            if not s.saturated and not s.edge and s.centroid_method == "daofind"
            # The cutout (rounded centre +- half + 1 spare pixel) must lie inside the image.
            and half + 1 <= round(s.x) < width - half - 2
            and half + 1 <= round(s.y) < height - half - 2
        ),
        key=brightness_key,
    )  # fmt: skip
    isolation = config.isophote_isolation_fwhm * fwhm
    stack = []
    for s in pool:
        if np.count_nonzero(np.hypot(xy[:, 0] - s.x, xy[:, 1] - s.y) < isolation) > 1:
            continue  # a neighbour (the source itself is always within the radius)
        r, c = round(s.y), round(s.x)
        window = (slice(r - half - 1, r + half + 2), slice(c - half - 1, c + half + 2))
        if saturated_pixels[window].any():
            continue
        # One spare pixel on each side absorbs the sub-pixel shift, then is cropped.
        aligned = ndimage.shift(subtracted[window].astype(float), (r - s.y, c - s.x), order=3,
                                mode="nearest")[1:-1, 1:-1]  # fmt: skip
        total = aligned.sum()
        if total > 0:
            stack.append(aligned / total)
        if len(stack) >= config.isophote_calibration_max_stars:
            break
    if len(stack) < config.isophote_calibration_min_stars:
        return None, (
            f"only {len(stack)} isolated unsaturated stars for the isophote calibration "
            f"(need {config.isophote_calibration_min_stars})"
        )

    psf = np.median(np.array(stack), axis=0)  # PSF centre at pixel (half, half)
    peak_index = np.unravel_index(np.argmax(psf), psf.shape)
    rows, cols = np.mgrid[: psf.shape[0], : psf.shape[1]]
    areas, dxs, dys = [], [], []
    for fraction in ISOPHOTE_LEVELS:
        labels, _ = ndimage.label(psf > fraction * psf.max())
        region = labels == labels[peak_index]
        if region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any():
            break  # the isophote reaches the stack border: no longer fully measured
        areas.append(float(region.sum()))
        dxs.append(float(cols[region].mean() - half))
        dys.append(float(rows[region].mean() - half))
    if len(areas) < 2:
        return None, "the stacked PSF yielded fewer than two usable isophotes"
    order = np.argsort(areas, kind="stable")
    calibration = IsophoteCalibration(
        area=np.asarray(areas)[order],
        dx=np.asarray(dxs)[order],
        dy=np.asarray(dys)[order],
        n_stars=len(stack),
        stack_half_width=half,
    )
    return calibration, f"calibrated on {len(stack)} stars, areas up to {calibration.max_area:g} px"


def apply_astrometric_centroids(
    sources: Iterable[Source], calibration: IsophoteCalibration | None
) -> list[Source]:
    """Set ``astrometric_x/y/method/correction`` on every source (see module docstring)."""
    result = []
    for source in sources:
        if source.centroid_method != CENTROID_SATURATED_CORE:
            result.append(_with_astrometric(source, None, ASTROMETRIC_DETECTION))
            continue
        offset = calibration.correction(source.saturated_core_area) if calibration else None
        if offset is None:
            result.append(_with_astrometric(source, None, ASTROMETRIC_FALLBACK))
        else:
            xy = (source.x - offset[0], source.y - offset[1])
            result.append(_with_astrometric(source, xy, ASTROMETRIC_ISOPHOTE))
    return result


def astrometric_summary(
    sources: Iterable[Source], calibration: IsophoteCalibration | None, note: str, enabled: bool
) -> dict[str, Any]:
    """Diagnostics recorded in the detection metadata."""
    sources = list(sources)
    counts = {
        method: sum(s.astrometric_method == method for s in sources if s.accepted)
        for method in (ASTROMETRIC_DETECTION, ASTROMETRIC_ISOPHOTE, ASTROMETRIC_FALLBACK)
    }
    shifts = [s.astrometric_correction_px for s in sources
              if s.accepted and s.astrometric_method == ASTROMETRIC_ISOPHOTE]  # fmt: skip
    return {
        "enabled": enabled,
        "method": "isophote-calibrated saturated cores (image's own stacked unsaturated PSF)",
        "note": note,
        "accepted_by_method": counts,
        "median_correction_px": float(np.median(shifts)) if shifts else None,
        "max_correction_px": float(np.max(shifts)) if shifts else None,
        "calibration": calibration.to_dict() if calibration is not None else None,
    }


def _with_astrometric(source: Source, xy: tuple[float, float] | None, method: str) -> Source:
    """``xy=None`` keeps the detection centroid (``Source.astrometric_xy`` resolves it)."""
    if xy is None:
        return dataclasses.replace(
            source,
            astrometric_x=None,
            astrometric_y=None,
            astrometric_method=method,
            astrometric_correction_px=0.0,
        )
    x, y = float(xy[0]), float(xy[1])
    return dataclasses.replace(
        source,
        astrometric_x=x,
        astrometric_y=y,
        astrometric_method=method,
        astrometric_correction_px=float(math.hypot(x - source.x, y - source.y)),
    )

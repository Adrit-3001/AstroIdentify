"""Stellar candidate detection with DAOFIND (photutils ``DAOStarFinder``).

DAOFIND convolves the background-subtracted plane with a Gaussian kernel of the stellar
FWHM, keeps local maxima above a threshold, and centroids each by fitting 1-D Gaussians to
the marginal x/y profiles, which gives the accurate positions plate solving needs.

* The threshold is an array, ``detection_sigma * local RMS map``, so it follows spatially
  varying noise. DAOFIND scales it by the kernel's relative error (``scale_threshold``), so
  it acts as a limit on the fitted peak amplitude in units of the per-pixel RMS.
* DAOFIND's built-in sharpness/roundness cuts are disabled. Every thresholded peak becomes
  a raw candidate, and all quality decisions happen in ``detection.filtering``, which
  records a reason for each rejection instead of dropping candidates silently.
* ``peak_max`` is not used, so saturated stars are detected rather than discarded.
* DAOFIND silently drops objects it cannot fit (typically bright stars far broader than
  the kernel), so a supplementary peak search adds them back (see
  :func:`find_supplementary_peaks`).

The FWHM is either configured or estimated from the image (see :func:`estimate_fwhm`).
"""

from __future__ import annotations

import logging
import math
import warnings

import numpy as np
from photutils.detection import DAOStarFinder, find_peaks
from photutils.psf import fit_fwhm
from photutils.utils import NoDetectionsWarning
from scipy import ndimage
from scipy.spatial import cKDTree

from astroidentify.config import DetectionConfig
from astroidentify.detection.types import (
    Candidates,
    DetectionPlane,
    FwhmEstimate,
    LocalBackground,
)

CENTROID_DAOFIND = "daofind"
CENTROID_PEAK_COM = "peak_com"
# 2 * sqrt(2 ln 2): converts a Gaussian sigma to its FWHM.
GAUSSIAN_SIGMA_TO_FWHM = 2.0 * math.sqrt(2.0 * math.log(2.0))

logger = logging.getLogger(__name__)

# Fit box half-width for FWHM estimation, in units of the current FWHM estimate: wide
# enough to include the wings that constrain the width.
_FIT_HALF_WIDTH_FWHM = 1.5
# Stop iterating the FWHM estimate once it changes by less than this fraction.
_FWHM_CONVERGENCE = 0.02
_FWHM_MAX_ITERATIONS = 6
# Half-width (in FWHM) of the local-maximum neighbourhood and centroid box used by the
# supplementary peak search; together they span about the minimum separation.
_PEAK_BOX_HALF_WIDTH_FWHM = 1.25


def find_candidates(
    background: LocalBackground,
    plane: DetectionPlane,
    fwhm: float,
    detection_sigma: float,
    min_separation_fwhm: float,
) -> Candidates:
    """DAOFIND candidates plus supplementary peaks that DAOFIND could not fit."""
    daofind = find_daofind_candidates(background, plane, fwhm, detection_sigma, min_separation_fwhm)
    extra = find_supplementary_peaks(
        background, plane, fwhm, detection_sigma, daofind.xy, min_separation_fwhm
    )
    if len(extra):
        logger.info("Added %d peaks that DAOFIND could not fit", len(extra))
    return Candidates.concatenate(daofind, extra)


def find_daofind_candidates(
    background: LocalBackground,
    plane: DetectionPlane,
    fwhm: float,
    detection_sigma: float,
    min_separation_fwhm: float,
) -> Candidates:
    """Run DAOFIND on the background-subtracted plane."""
    finder = DAOStarFinder(
        threshold=detection_sigma * background.rms,
        fwhm=fwhm,
        min_separation=min_separation_fwhm * fwhm,
        exclude_border=False,
        peak_max=None,
        sharpness_range=None,
        roundness_range=None,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=NoDetectionsWarning)
        table = finder.find_stars(
            background.subtracted, mask=plane.invalid_mask if plane.has_invalid else None
        )
    if table is None or len(table) == 0:
        return Candidates.empty()

    def column(name: str) -> np.ndarray:
        return np.asarray(table[name], dtype=float)

    return Candidates(
        x=column("x_centroid"),
        y=column("y_centroid"),
        peak=column("peak"),
        flux=column("flux"),
        sharpness=column("sharpness"),
        roundness1=column("roundness1"),
        roundness2=column("roundness2"),
        method=[CENTROID_DAOFIND] * len(table),
    )


def find_supplementary_peaks(
    background: LocalBackground,
    plane: DetectionPlane,
    fwhm: float,
    detection_sigma: float,
    existing_xy: np.ndarray,
    min_separation_fwhm: float,
) -> Candidates:
    """Find significant peaks that DAOFIND did not return.

    DAOFIND silently discards objects whose Gaussian-kernel fit amplitude is not positive,
    which happens for bright stars much broader than the kernel (halos, diffraction spikes,
    flat saturated tops). Local maxima of the plane smoothed with a Gaussian of the stellar
    FWHM that exceed the same ``detection_sigma * RMS`` threshold, and have no DAOFIND
    candidate within the minimum separation, are added. They are centroided by centre of
    mass (negative pixels clipped) in a box around the peak, and have no DAOFIND shape
    statistics (NaN).
    """
    subtracted = np.asarray(background.subtracted, dtype=float)
    smoothed = ndimage.gaussian_filter(subtracted, fwhm / GAUSSIAN_SIGMA_TO_FWHM)
    box = 2 * math.ceil(_PEAK_BOX_HALF_WIDTH_FWHM * fwhm) + 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=NoDetectionsWarning)
        peaks = find_peaks(
            smoothed,
            threshold=detection_sigma * background.rms,
            box_size=box,
            mask=plane.invalid_mask if plane.has_invalid else None,
        )
    if peaks is None or len(peaks) == 0:
        return Candidates.empty()

    peak_xy = np.column_stack([peaks["x_peak"], peaks["y_peak"]]).astype(float)
    if len(existing_xy):
        distance, _ = cKDTree(existing_xy).query(peak_xy)
        peak_xy = peak_xy[distance > min_separation_fwhm * fwhm]
    if len(peak_xy) == 0:
        return Candidates.empty()

    half = box // 2
    height, width = subtracted.shape
    rows = []
    for x_peak, y_peak in peak_xy.astype(int):
        r0, r1 = max(0, y_peak - half), min(height, y_peak + half + 1)
        c0, c1 = max(0, x_peak - half), min(width, x_peak + half + 1)
        cutout = np.clip(subtracted[r0:r1, c0:c1], 0.0, None)
        total = float(cutout.sum())
        if total <= 0:
            continue
        row_idx, col_idx = np.indices(cutout.shape)
        rows.append(
            (
                c0 + float((col_idx * cutout).sum()) / total,
                r0 + float((row_idx * cutout).sum()) / total,
                float(cutout.max()),
                total,
            )
        )
    if not rows:
        return Candidates.empty()
    x, y, peak, flux = (np.array(values) for values in zip(*rows, strict=True))
    nan = np.full(len(rows), np.nan)
    return Candidates(
        x=x,
        y=y,
        peak=peak,
        flux=flux,
        sharpness=nan,
        roundness1=nan.copy(),
        roundness2=nan.copy(),
        method=[CENTROID_PEAK_COM] * len(rows),
    )


def estimate_fwhm(
    background: LocalBackground,
    plane: DetectionPlane,
    saturated_pixels: np.ndarray,
    config: DetectionConfig,
) -> tuple[FwhmEstimate, tuple[str, ...]]:
    """Return the configured FWHM, or estimate it from bright, unsaturated, isolated stars.

    Estimation: detect bright candidates (``fwhm_estimation_sigma``) with the initial
    guess, fit a circular 2-D Gaussian to up to ``fwhm_estimation_max_stars`` of the
    brightest whose fit box is inside the image and free of saturated pixels, and take the
    median. The fit box grows with the estimate, so iterate until it converges. Saturated
    stars are excluded because their clipped cores widen the fit.
    """
    if config.fwhm is not None:
        return FwhmEstimate(value=config.fwhm, method="configured"), ()

    guess = config.fwhm_initial_guess
    # Only DAOFIND detections: they are the compact, star-like profiles the fit assumes.
    bright = find_daofind_candidates(
        background, plane, guess, config.fwhm_estimation_sigma, config.min_separation_fwhm
    )
    if len(bright) == 0:
        return _fallback(config, "no bright stars found for FWHM estimation")

    xy = bright.xy[np.argsort(bright.flux)[::-1]]
    sat_integral = integral_image(saturated_pixels | plane.invalid_mask)

    estimate = guess
    fitted = np.array([])
    iterations = 0
    for iterations in range(1, _FWHM_MAX_ITERATIONS + 1):  # noqa: B007 (used after loop)
        half = max(2, math.ceil(_FIT_HALF_WIDTH_FWHM * estimate))
        usable = _clean_boxes(xy, half, plane.shape, sat_integral)
        chosen = xy[usable][: config.fwhm_estimation_max_stars]
        if len(chosen) < config.fwhm_estimation_min_stars:
            return _fallback(
                config,
                f"only {len(chosen)} unsaturated stars available for FWHM estimation "
                f"(need {config.fwhm_estimation_min_stars})",
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # individual non-converging fits are filtered
            fitted = np.asarray(
                fit_fwhm(background.subtracted, xypos=chosen, fit_shape=2 * half + 1), float
            )
        fitted = fitted[np.isfinite(fitted) & (fitted > 0) & (fitted < 2 * config.fwhm_max)]
        if len(fitted) < config.fwhm_estimation_min_stars:
            return _fallback(config, "FWHM fits failed for most stars")
        new_estimate = float(np.median(fitted))
        converged = abs(new_estimate - estimate) < _FWHM_CONVERGENCE * estimate
        estimate = new_estimate
        if converged:
            break

    notes: list[str] = []
    clamped = min(max(estimate, config.fwhm_min), config.fwhm_max)
    if clamped != estimate:
        notes.append(
            f"estimated FWHM {estimate:.3g} px is outside [{config.fwhm_min}, "
            f"{config.fwhm_max}]; using {clamped:.3g} px"
        )
    for note in notes:
        logger.warning(note)
    spread = (float(np.percentile(fitted, 16)), float(np.percentile(fitted, 84)))
    logger.info("Estimated FWHM %.3g px from %d stars", clamped, len(fitted))
    return (
        FwhmEstimate(
            value=clamped,
            method="estimated",
            n_stars=len(fitted),
            iterations=iterations,
            spread=spread,
        ),
        tuple(notes),
    )


def _fallback(config: DetectionConfig, reason: str) -> tuple[FwhmEstimate, tuple[str, ...]]:
    message = f"{reason}; using the initial FWHM guess {config.fwhm_initial_guess} px"
    logger.warning(message)
    return FwhmEstimate(value=config.fwhm_initial_guess, method="initial_guess"), (message,)


def integral_image(mask: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero row/column prepended, for O(1) box counts."""
    integral = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    integral[1:, 1:] = np.cumsum(np.cumsum(mask, axis=0, dtype=np.int64), axis=1)
    return integral


def box_counts(integral: np.ndarray, xy: np.ndarray, half: int) -> np.ndarray:
    """Count flagged pixels in ``(2 half + 1)^2`` boxes centred on each ``(x, y)``.

    Boxes are clipped to the image.
    """
    height, width = integral.shape[0] - 1, integral.shape[1] - 1
    cols = np.rint(xy[:, 0]).astype(int)
    rows = np.rint(xy[:, 1]).astype(int)
    r0 = np.clip(rows - half, 0, height)
    r1 = np.clip(rows + half + 1, 0, height)
    c0 = np.clip(cols - half, 0, width)
    c1 = np.clip(cols + half + 1, 0, width)
    return integral[r1, c1] - integral[r0, c1] - integral[r1, c0] + integral[r0, c0]


def _clean_boxes(
    xy: np.ndarray, half: int, shape: tuple[int, int], flagged_integral: np.ndarray
) -> np.ndarray:
    height, width = shape
    cols = np.rint(xy[:, 0])
    rows = np.rint(xy[:, 1])
    inside = (
        (cols - half >= 0) & (rows - half >= 0) & (cols + half < width) & (rows + half < height)
    )
    return inside & (box_counts(flagged_integral, xy, half) == 0)

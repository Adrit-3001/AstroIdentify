"""Per-candidate measurements: photometry, SNR, width, saturation and edge proximity.

* Flux: exact-overlap circular aperture photometry on the background-subtracted plane.
* Error/SNR: ``flux_err = sqrt(sum(rms^2)) * noise_factor`` over the aperture. The RMS map
  describes per-pixel noise, which understates aperture noise when noise is spatially
  correlated (JPEG compression, demosaicing, stacking/resampling). ``noise_factor`` is
  measured empirically with "empty apertures": the same aperture placed on a regular grid
  of source-free positions, whose robust scatter is compared with the per-pixel
  prediction. It is 1 for white noise. Source photon noise is not included because the
  detector gain is generally unknown, so SNR is a background-limited ranking statistic,
  not a calibrated uncertainty.
* Width: effective FWHM from the flux/peak ratio (see ``SOURCE_FIELDS``). It is cheap for
  thousands of candidates, unlike per-source Gaussian fits.
* Saturation: count of saturated pixels (any channel) inside the aperture.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from photutils.aperture import CircularAperture, aperture_photometry
from scipy.spatial import cKDTree

from astroidentify.config import DetectionConfig
from astroidentify.detection.detector import GAUSSIAN_SIGMA_TO_FWHM, box_counts, integral_image
from astroidentify.detection.types import Candidates, DetectionPlane, LocalBackground, Source
from astroidentify.preprocessing.background import MAD_TO_SIGMA

logger = logging.getLogger(__name__)

# Empty apertures must be at least this many aperture radii from any candidate centroid,
# so that neither the candidate's aperture nor its brightest wings overlap them.
EMPTY_APERTURE_EXCLUSION_RADII = 2.0


def empirical_noise_factor(
    background: LocalBackground,
    candidate_xy: np.ndarray,
    radius: float,
    flagged_pixels: np.ndarray,
    min_count: int,
) -> tuple[float, int]:
    """Return ``(factor, n_apertures)``: measured / predicted aperture-sum noise (>= 1).

    Apertures of ``radius`` are placed on a non-overlapping grid, keeping those away from
    all candidates and free of ``flagged_pixels`` (saturated or invalid). The factor is the
    robust (MAD) standard deviation of ``sum / predicted_error`` over those apertures.
    Returns ``(1.0, n)`` if fewer than ``min_count`` apertures are available.
    """
    height, width = background.subtracted.shape
    step = math.ceil(2 * radius) + 1
    margin = math.ceil(radius) + 1
    grid_y, grid_x = np.mgrid[margin : height - margin : step, margin : width - margin : step]
    grid = np.column_stack([grid_x.ravel(), grid_y.ravel()]).astype(float)
    if len(grid) and len(candidate_xy):
        distance, _ = cKDTree(candidate_xy).query(grid)
        grid = grid[distance >= EMPTY_APERTURE_EXCLUSION_RADII * radius]
    if len(grid):
        grid = grid[box_counts(integral_image(flagged_pixels), grid, margin) == 0]
    if len(grid) < min_count:
        return 1.0, len(grid)

    phot = aperture_photometry(
        background.subtracted, CircularAperture(grid, r=radius), error=background.rms
    )
    z = np.asarray(phot["aperture_sum"], float) / np.asarray(phot["aperture_sum_err"], float)
    scatter = MAD_TO_SIGMA * float(np.median(np.abs(z - np.median(z))))
    # Never below 1: sampling scatter must not make SNRs more optimistic than white noise.
    return max(1.0, scatter), len(grid)


def measure_sources(
    candidates: Candidates,
    xy: np.ndarray,
    centroid_methods: list[str],
    core_axis_ratio: np.ndarray,
    core_area: np.ndarray,
    plane: DetectionPlane,
    background: LocalBackground,
    saturated_pixels: np.ndarray,
    fwhm: float,
    noise_factor: float,
    config: DetectionConfig,
) -> list[Source]:
    """Measure every candidate at ``xy``; returned unfiltered, unnumbered, in table order."""
    radius = config.aperture_radius_fwhm * fwhm
    apertures = CircularAperture(xy, r=radius)
    mask = plane.invalid_mask if plane.has_invalid else None

    phot = aperture_photometry(
        background.subtracted, apertures, error=background.rms, mask=mask, method="exact"
    )
    flux = np.asarray(phot["aperture_sum"], float)
    flux_err = np.asarray(phot["aperture_sum_err"], float) * noise_factor
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(flux_err > 0, flux / flux_err, np.nan)

    saturated_counts = np.asarray(
        aperture_photometry(saturated_pixels.astype(np.float32), apertures, method="center")[
            "aperture_sum"
        ],
        float,
    )
    peak = candidates.peak
    with np.errstate(divide="ignore", invalid="ignore"):
        fwhm_eff = GAUSSIAN_SIGMA_TO_FWHM * np.sqrt(flux / (2.0 * math.pi * peak))
    fwhm_eff = np.where((flux > 0) & (peak > 0), fwhm_eff, np.nan)

    height, width = plane.shape
    edge_distance = np.minimum.reduce(
        [xy[:, 0], xy[:, 1], (width - 1) - xy[:, 0], (height - 1) - xy[:, 1]]
    )
    rows = np.clip(np.rint(xy[:, 1]).astype(int), 0, height - 1)
    cols = np.clip(np.rint(xy[:, 0]).astype(int), 0, width - 1)
    local_bkg = background.background[rows, cols]
    local_rms = background.rms[rows, cols]
    edge_limit = config.edge_flag_fwhm * fwhm

    return [
        Source(
            source_id=0,
            x=float(xy[i, 0]),
            y=float(xy[i, 1]),
            flux=float(flux[i]),
            flux_err=float(flux_err[i]),
            snr=float(snr[i]),
            peak=float(peak[i]),
            fwhm=float(fwhm_eff[i]),
            sharpness=float(candidates.sharpness[i]),
            roundness1=float(candidates.roundness1[i]),
            roundness2=float(candidates.roundness2[i]),
            local_background=float(local_bkg[i]),
            local_rms=float(local_rms[i]),
            edge_distance=float(edge_distance[i]),
            n_saturated_pixels=round(saturated_counts[i]),
            saturated=bool(saturated_counts[i] > 0),
            edge=bool(edge_distance[i] < edge_limit),
            centroid_method=centroid_methods[i],
            saturated_core_axis_ratio=float(core_axis_ratio[i]),
            saturated_core_area=int(core_area[i]),
        )
        for i in range(len(xy))
    ]

"""WCS refinement against the catalogue and deterministic one-to-one crossmatching.

Pure array logic: no network access, so everything here is testable offline.

**Why refine the WCS.** A plate solution is fitted to a small set of reference stars. On the
Unistellar benchmark these were the 25 brightest, heavily saturated stars, whose saturated-
core centroids are biased by up to ~5 px relative to ordinary stars on comet-shaped
(coma-affected) PSFs. The plate solution was internally consistent with them but offset by
~4" for every other star. Refinement fits a new WCS to many bright, isolated, unsaturated
detection/catalogue pairs, so typical stars, not a few biased ones, define the solution.

1. **Registration pairs** (through the input WCS): among the eligible catalogue stars,
   mutually nearest detection/catalogue pairs within ``registration_radius_arcsec`` whose
   second-nearest catalogue star is ``registration_isolation_ratio`` times farther.
   Saturated detections are excluded from the fit.
2. **Fit** with Astropy ``fit_wcs_from_points`` (TAN, optional SIP), iteratively discarding
   pairs whose residual exceeds ``refine_clip_sigma`` per-axis sigma (estimated robustly from
   the median residual magnitude, which is Rayleigh-distributed).
   Note: despite its docstring mentioning
   FITS 1-based pixels, Astropy's implementation evaluates ``wcs_pix2world(x, y, 0)``, so it
   expects 0-based pixels: canonical coordinates are passed unchanged (verified by test).
3. **Final match** through the refined WCS at the tight ``match_radius_arcsec``.

**One-to-one assignment.** Candidate edges are all detection/catalogue pairs within the
radius (angular separation, the primary criterion). They are processed in ascending order
of ``(separation, gaia source_id, detection source_id)``; each edge is accepted if neither
end is already matched. This deterministic greedy assignment guarantees one-to-one matches,
resolves conflicts in favour of the closer pair, and breaks exact ties by ID.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.wcs.utils import fit_wcs_from_points
from scipy.spatial import cKDTree

from astroidentify.astrometry.wcs import pixel_to_sky

logger = logging.getLogger(__name__)

_MAX_CLIP_ITERATIONS = 5
# Residual magnitudes of 2-D Gaussian errors follow a Rayleigh distribution whose median is
# sqrt(2 ln 2) = 1.1774 times the per-axis sigma.
_RAYLEIGH_MEDIAN_TO_SIGMA = 1.0 / np.sqrt(2.0 * np.log(2.0))
# Never clip residuals below this (arcsec): clipping targets mis-paired stars, not noise.
_CLIP_FLOOR_ARCSEC = 0.1


@dataclass(frozen=True)
class Edge:
    """A candidate or accepted detection/catalogue association (indices into the inputs)."""

    detection_index: int
    catalog_index: int
    separation_arcsec: float


@dataclass(frozen=True)
class Refinement:
    """Outcome of WCS refinement (``wcs`` is ``None`` if it was not possible)."""

    wcs: WCS | None
    n_pairs: int
    n_used: int
    input_median_offset_arcsec: float | None
    input_mean_offset_px: tuple[float, float] | None
    residual_median_arcsec: float | None
    residual_rms_arcsec: float | None
    reason: str


def eligible_catalog_indices(
    magnitudes: np.ndarray, in_image: np.ndarray, n_detections: int, factor: float | None
) -> np.ndarray:
    """In-image catalogue rows eligible for matching: the brightest ``factor * N`` (stable)."""
    candidates = np.flatnonzero(in_image)
    if factor is None:
        return candidates
    mags = np.where(np.isfinite(magnitudes[candidates]), magnitudes[candidates], np.inf)
    order = np.lexsort((candidates, mags))  # brightest first, ties by row index
    keep = max(1, int(np.ceil(factor * n_detections)))
    return np.sort(candidates[order[:keep]])


def registration_pairs(
    det_xy: np.ndarray,
    cat_xy: np.ndarray,
    radius_px: float,
    isolation_ratio: float,
) -> list[tuple[int, int]]:
    """Mutually nearest, isolated detection/catalogue pairs (indices into the inputs)."""
    if len(det_xy) == 0 or len(cat_xy) < 2:
        return []
    cat_tree = cKDTree(cat_xy)
    distance, nearest = cat_tree.query(det_xy, k=2)
    back = cKDTree(det_xy).query(cat_xy)[1]
    pairs = []
    for i in range(len(det_xy)):
        d1, d2 = distance[i]
        j = int(nearest[i, 0])
        if d1 <= radius_px and d2 >= isolation_ratio * d1 and back[j] == i:
            pairs.append((i, j))
    return pairs


def fit_refined_wcs(
    det_xy: np.ndarray,
    world: SkyCoord,
    sip_degree: int | None,
    clip_sigma: float,
    min_pairs: int,
) -> tuple[WCS | None, np.ndarray, str]:
    """Fit a WCS to canonical pixel positions; returns ``(wcs, used_mask, reason)``."""
    used = np.ones(len(det_xy), bool)
    if used.sum() < min_pairs:
        return None, used, f"only {used.sum()} registration pairs (need {min_pairs})"
    wcs = None
    for _ in range(_MAX_CLIP_ITERATIONS):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Canonical (0-based) pixels: Astropy's fitter evaluates with origin=0.
            wcs = fit_wcs_from_points(
                (det_xy[used, 0], det_xy[used, 1]), world[used], projection="TAN",
                sip_degree=sip_degree,
            )  # fmt: skip
        residual = _residual_arcsec(wcs, det_xy, world)
        sigma_axis = np.median(residual[used]) * _RAYLEIGH_MEDIAN_TO_SIGMA
        keep = residual <= max(clip_sigma * sigma_axis, _CLIP_FLOOR_ARCSEC)
        if np.array_equal(keep, used) or keep.sum() < min_pairs:
            break
        used = keep
    return wcs, used, "refined"


def refine_wcs(
    input_wcs: WCS,
    det_xy: np.ndarray,
    det_saturated: np.ndarray,
    cat_xy_input: np.ndarray,
    cat_world: SkyCoord,
    pixel_scale_arcsec: float,
    *,
    radius_arcsec: float,
    isolation_ratio: float,
    sip_degree: int | None,
    clip_sigma: float,
    min_pairs: int,
) -> Refinement:
    """Refine ``input_wcs`` against catalogue positions (see module docstring)."""
    pairs = registration_pairs(
        det_xy, cat_xy_input, radius_arcsec / pixel_scale_arcsec, isolation_ratio
    )
    pairs = [(i, j) for i, j in pairs if not det_saturated[i]]
    if not pairs:
        return Refinement(None, 0, 0, None, None, None, None, "no registration pairs found")
    di = np.array([i for i, _ in pairs])
    cj = np.array([j for _, j in pairs])
    offsets = det_xy[di] - cat_xy_input[cj]
    input_offset = _residual_arcsec(input_wcs, det_xy[di], cat_world[cj])
    wcs, used, reason = fit_refined_wcs(
        det_xy[di], cat_world[cj], sip_degree, clip_sigma, min_pairs
    )
    common = {
        "n_pairs": len(pairs),
        "input_median_offset_arcsec": float(np.median(input_offset)),
        "input_mean_offset_px": (float(offsets[:, 0].mean()), float(offsets[:, 1].mean())),
    }
    if wcs is None:
        return Refinement(None, n_used=0, residual_median_arcsec=None,
                          residual_rms_arcsec=None, reason=reason, **common)  # fmt: skip
    residual = _residual_arcsec(wcs, det_xy[di][used], cat_world[cj][used])
    return Refinement(
        wcs,
        n_used=int(used.sum()),
        residual_median_arcsec=float(np.median(residual)),
        residual_rms_arcsec=float(np.sqrt(np.mean(residual**2))),
        reason=reason,
        **common,
    )


def candidate_edges(det_sky: SkyCoord, cat_sky: SkyCoord, radius_arcsec: float) -> list[Edge]:
    """All pairs within ``radius_arcsec`` (angular separation)."""
    if len(det_sky) == 0 or len(cat_sky) == 0:
        return []
    cat_index, det_index, separation, _ = det_sky.search_around_sky(
        cat_sky, radius_arcsec * u.arcsec
    )
    return [
        Edge(int(d), int(c), float(s))
        for d, c, s in zip(det_index, cat_index, separation.arcsec, strict=True)
    ]


def assign_one_to_one(
    edges: list[Edge], detection_ids: np.ndarray, catalog_ids: np.ndarray
) -> list[Edge]:
    """Deterministic greedy one-to-one assignment by ascending separation, ties by IDs."""
    ordered = sorted(
        edges,
        key=lambda e: (
            e.separation_arcsec,
            int(catalog_ids[e.catalog_index]),
            int(detection_ids[e.detection_index]),
        ),
    )
    used_detections: set[int] = set()
    used_catalog: set[int] = set()
    accepted = []
    for edge in ordered:
        if edge.detection_index in used_detections or edge.catalog_index in used_catalog:
            continue
        used_detections.add(edge.detection_index)
        used_catalog.add(edge.catalog_index)
        accepted.append(edge)
    return accepted


def _residual_arcsec(wcs: WCS, xy: np.ndarray, world: SkyCoord) -> np.ndarray:
    ra, dec = pixel_to_sky(wcs, xy[:, 0], xy[:, 1])
    return SkyCoord(ra, dec, unit="deg").separation(world).arcsec

"""Astrometric evidence: how well the WCS places things, globally and near each object.

**Field grade** (``field_astrometry``), from the residual statistics that describe the WCS
actually used (see ``evidence.features``):

* precise:  at least ``precise_min_gaia_matches`` Gaia stars, median residual at most
  ``precise_max_median_residual_px``;
* adequate: at least ``adequate_min_gaia_matches``, median at most
  ``adequate_max_median_residual_px``. The plate solver's own match statistics, which are
  a few bright index stars, can reach at most "adequate";
* poor: anything worse; unavailable: no applicable residual statistics.

**Positional scale r50** (``positional_scale``): the median radial residual of matched Gaia
stars, i.e. half of all real star-detection pairs lie closer than r50. Within the WCS used,
it combines WCS error, centroid error and catalogue error, exactly what an object-detection
separation is exposed to. It is local (the ``local_residual_matches`` nearest matches) when
those lie within ``local_max_radius_fraction`` of the image diagonal, otherwise field-level.
A numerical floor ``min_position_scale_px`` keeps normalization stable. Missing statistics
leave r50 ``None``; nothing is invented.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

from astroidentify.config import EvidenceConfig
from astroidentify.evidence.features import (
    RESIDUALS_FINAL_WCS,
    RESIDUALS_INPUT_OFFSET,
    RESIDUALS_PLATE_SOLVER,
    EvidenceInputs,
)
from astroidentify.evidence.types import (
    ASTROMETRY_ADEQUATE,
    ASTROMETRY_POOR,
    ASTROMETRY_PRECISE,
    ASTROMETRY_UNAVAILABLE,
    AstrometryEvidence,
)


def grade_astrometry(
    n_matches: int | None, median_px: float | None, config: EvidenceConfig, *, max_grade=None
) -> str:
    if n_matches is None or median_px is None or not math.isfinite(median_px):
        return ASTROMETRY_UNAVAILABLE
    if (
        n_matches >= config.precise_min_gaia_matches
        and median_px <= config.precise_max_median_residual_px
    ):
        grade = ASTROMETRY_PRECISE
    elif (
        n_matches >= config.adequate_min_gaia_matches
        and median_px <= config.adequate_max_median_residual_px
    ):
        grade = ASTROMETRY_ADEQUATE
    else:
        grade = ASTROMETRY_POOR
    if max_grade == ASTROMETRY_ADEQUATE and grade == ASTROMETRY_PRECISE:
        grade = ASTROMETRY_ADEQUATE
    return grade


def field_astrometry(inputs: EvidenceInputs, config: EvidenceConfig) -> AstrometryEvidence:
    """Field-level astrometric evidence for the WCS that placed the objects."""
    scale = inputs.pixel_scale_arcsec
    wcs = inputs.objects_summary.get("wcs") or {}
    cat = inputs.catalog_summary or {}
    summary = cat.get("summary") or {}
    refinement = cat.get("wcs_refinement") or {}
    plate = inputs.plate_solution or {}
    stats = plate.get("match_statistics") or {}
    relation = inputs.residual_relation

    n, median_px, source = None, None, None
    max_grade = None
    if relation == RESIDUALS_FINAL_WCS:
        n, median_px, source = (
            summary.get("matches"),
            summary.get("median_residual_px"),
            "field_gaia",
        )
    elif relation == RESIDUALS_INPUT_OFFSET:
        offset = refinement.get("input_wcs_median_offset_arcsec")
        n = refinement.get("registration_pairs")
        median_px, source = (offset / scale if offset is not None else None), "input_wcs_offset"
    elif relation == RESIDUALS_PLATE_SOLVER:
        n, median_px, source = (
            stats.get("n_matched"),
            stats.get("median_residual_px"),
            "plate_solver",
        )
        max_grade = ASTROMETRY_ADEQUATE
    grade = grade_astrometry(n, median_px, config, max_grade=max_grade)
    r50 = None if median_px is None else max(float(median_px), config.min_position_scale_px)
    return AstrometryEvidence(
        grade=grade,
        wcs_source=wcs.get("source"),
        wcs_refined=wcs.get("refined"),
        plate_solution_mode=(inputs.objects_summary.get("inputs") or {}).get("plate_solution_mode"),
        gaia_matches=summary.get("matches"),
        gaia_match_fraction=summary.get("detection_match_fraction"),
        gaia_median_residual_px=summary.get("median_residual_px"),
        gaia_rms_residual_px=summary.get("rms_residual_px"),
        gaia_median_residual_arcsec=summary.get("median_residual_arcsec"),
        plate_solver_median_residual_arcsec=stats.get("median_residual_arcsec"),
        plate_solver_matches=stats.get("n_matched"),
        epoch_propagated=summary.get("epoch_propagated") if summary else None,
        r50_px=r50,
        r50_arcsec=None if r50 is None else r50 * scale,
        r50_source=source if r50 is not None else None,
        n_local_matches=None,
        local_radius_px=None,
    )


class LocalResiduals:
    """Nearest-neighbour access to per-star Gaia residuals of the WCS used."""

    def __init__(self, inputs: EvidenceInputs, config: EvidenceConfig) -> None:
        self.config = config
        self.limit = config.local_max_radius_fraction * math.hypot(inputs.width, inputs.height)
        gaia = inputs.gaia
        usable = inputs.residual_relation == RESIDUALS_FINAL_WCS and gaia is not None
        self.residuals = gaia.residual_px if usable else None
        self.tree = cKDTree(np.column_stack([gaia.x, gaia.y])) if usable else None

    def scale_at(self, field: AstrometryEvidence, x: float, y: float, pixel_scale: float):
        """``(r50_px, source, n_local, radius_px)`` at a position (field value as fallback)."""
        k = self.config.local_residual_matches
        if (
            self.tree is not None
            and len(self.residuals) >= k
            and math.isfinite(x)
            and math.isfinite(y)
        ):
            distance, index = self.tree.query([x, y], k=k)
            radius = float(distance[-1])
            if radius <= self.limit:
                r50 = max(
                    float(np.median(self.residuals[index])), self.config.min_position_scale_px
                )
                return r50, "local_gaia", k, radius
        return field.r50_px, field.r50_source, None, None

"""Data models for Milestone 4 catalogue matching.

Pixel coordinates use AstroIdentify's canonical convention (0-based array x/y, pixel centres
at integers, y down); see ``astroidentify.detection.types.COORDINATE_CONVENTION``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from astroidentify.astrometry.types import SkyPosition


@dataclass(frozen=True)
class QueryRegion:
    """A cone that safely covers the solved image footprint.

    Attributes:
        centre: Sky position of the image centre (from the WCS).
        radius_deg: Farthest-corner distance plus ``margin_arcsec``.
        corner_distances_deg: Angular distance from the centre to each image corner.
        margin_arcsec: Safety margin included in ``radius_deg``.
        image_width, image_height: Image size the region was derived for.
    """

    centre: SkyPosition
    radius_deg: float
    corner_distances_deg: dict[str, float]
    margin_arcsec: float
    image_width: int
    image_height: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": "cone",
            "centre": self.centre.to_dict(),
            "radius_deg": self.radius_deg,
            "radius_arcmin": self.radius_deg * 60.0,
            "corner_distances_deg": self.corner_distances_deg,
            "margin_arcsec": self.margin_arcsec,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "method": "farthest WCS image corner from the WCS centre plus margin; rows are "
            "then filtered exactly by projecting them into the image",
        }


@dataclass(frozen=True, eq=False)
class CatalogTable:
    """Catalogue rows as column arrays (missing values are NaN for float columns)."""

    columns: dict[str, np.ndarray]

    def __len__(self) -> int:
        return len(self.columns["source_id"]) if "source_id" in self.columns else 0

    def __getitem__(self, name: str) -> np.ndarray:
        return self.columns[name]

    def get(self, name: str) -> np.ndarray | None:
        return self.columns.get(name)

    def take(self, indices: np.ndarray) -> CatalogTable:
        return CatalogTable({name: values[indices] for name, values in self.columns.items()})


@dataclass(frozen=True, eq=False)
class CatalogQueryResult:
    """A catalogue response plus everything needed to reproduce/understand it."""

    provider: str
    release: str
    table: str
    service_url: str
    region: QueryRegion
    adql: str
    columns: tuple[str, ...]
    row_limit: int
    rows: CatalogTable
    truncated: bool
    origin: str  # "live" or "cache"
    queried_at: str  # UTC ISO time of the original (live) query
    query_seconds: float | None  # live network time; None for cache hits
    cache_path: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "release": self.release,
            "table": self.table,
            "service_url": self.service_url,
            "region": self.region.to_dict(),
            "adql": self.adql,
            "columns": list(self.columns),
            "filters": "none beyond the cone (no magnitude or quality cuts at query time)",
            "row_limit": self.row_limit,
            "rows_returned": len(self.rows),
            "truncated": self.truncated,
            "origin": self.origin,
            "queried_at": self.queried_at,
            "query_seconds": self.query_seconds,
            "cache_path": self.cache_path,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EpochInfo:
    """Which sky coordinates were projected: catalogue epoch or propagated positions."""

    propagated: bool
    catalog_epoch: float | None
    observation_epoch: str | None
    observation_epoch_source: str | None
    n_propagated: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class ProjectedCatalog:
    """Catalogue rows projected into canonical image pixels.

    ``ra``/``dec`` are the coordinates actually projected (propagated if ``epoch.propagated``).
    ``in_image`` marks rows inside the image (plus the configured edge margin).
    """

    rows: CatalogTable
    ra: np.ndarray
    dec: np.ndarray
    x: np.ndarray
    y: np.ndarray
    in_image: np.ndarray
    epoch: EpochInfo


@dataclass(frozen=True)
class CatalogMatch:
    """One detection associated one-to-one with one catalogue star."""

    detection_source_id: int
    gaia_source_id: int
    observed_x_px: float
    observed_y_px: float
    predicted_x_px: float
    predicted_y_px: float
    residual_px: float
    residual_arcsec: float
    catalog_ra_deg: float
    catalog_dec_deg: float
    projected_ra_deg: float
    projected_dec_deg: float
    epoch_propagated: bool
    phot_g_mean_mag: float
    phot_bp_mean_mag: float
    phot_rp_mean_mag: float
    parallax: float
    pmra: float
    pmdec: float
    detection_flux: float
    detection_snr: float
    detection_saturated: bool
    detection_edge: bool
    n_candidates: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchSummary:
    """Objective crossmatch metrics (no identification or confidence)."""

    catalog: str
    rows_returned: int
    rows_in_image: int
    rows_eligible: int
    detections_considered: int
    matches: int
    detection_match_fraction: float | None
    catalog_match_fraction: float | None
    catalog_match_fraction_eligible: float | None
    median_residual_arcsec: float | None
    rms_residual_arcsec: float | None
    max_residual_arcsec: float | None
    median_residual_px: float | None
    rms_residual_px: float | None
    mean_offset_x_px: float | None
    mean_offset_y_px: float | None
    unmatched_detections: int
    unmatched_catalog_in_image: int
    unmatched_catalog_eligible: int
    ambiguous_detections: int
    epoch_propagated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class CatalogMatchResult:
    """Everything produced by catalogue matching."""

    query: CatalogQueryResult
    projected: ProjectedCatalog
    matches: tuple[CatalogMatch, ...]
    unmatched_detection_ids: tuple[int, ...]
    unmatched_catalog_ids: tuple[int, ...]
    summary: MatchSummary
    match_radius_arcsec: float
    match_radius_px: float
    pixel_scale_arcsec: float
    eligible_catalog_mask: np.ndarray
    wcs_used: Any  # astropy.wcs.WCS actually used for the final match
    wcs_refined: bool
    refinement: Any  # catalogs.matching.Refinement or None
    detections: tuple[Any, ...]  # accepted Milestone 2 Source objects considered
    warnings: tuple[str, ...] = field(default_factory=tuple)

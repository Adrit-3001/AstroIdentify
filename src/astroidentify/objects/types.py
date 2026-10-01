"""Data models for Milestone 5 object identification.

Pixel coordinates use AstroIdentify's canonical convention (0-based array x/y, pixel centres
at integers, y down); see ``astroidentify.detection.types.COORDINATE_CONVENTION``.

An "identification" here means: *the WCS places this catalogued object at this location in
the image*. Image evidence (a point-source association or a local brightness contrast) is
recorded separately and is never turned into a confidence value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from astroidentify.catalogs.types import QueryRegion

# Field status of a catalogue row relative to the image.
FIELD_CENTRE_IN_IMAGE = "centre_in_image"
FIELD_EXTENT_OVERLAPS = "extent_overlaps_image"  # centre outside, catalogued extent overlaps
FIELD_OUTSIDE = "outside_image"
FIELD_NOT_PROJECTABLE = "not_projectable"

# Identification status (catalogue presence; separate from image association).
STATUS_IN_FIELD = "catalogued_in_field"
STATUS_TYPE_EXCLUDED = "in_field_type_excluded"
STATUS_OUTSIDE = "outside_field"

# Image association kinds.
ASSOC_POINT_SOURCE = "point_source"  # compact object with an accepted detection nearby
ASSOC_NO_POINT_SOURCE = "none"  # compact object, no accepted detection within the radius
ASSOC_EXTENDED = "extended"  # extended object: footprint evidence instead of a point match
ASSOC_NOT_ATTEMPTED = "not_attempted"  # no detections supplied / centre outside the image

# Extent shapes.
EXTENT_ELLIPSE = "ellipse"  # major, minor and position angle catalogued
EXTENT_CIRCLE = "circle"  # only a usable major axis: circle of that diameter (a superset)
EXTENT_NONE = "none"  # no size catalogued: drawn as a position marker only


@dataclass(frozen=True)
class ObjectExtent:
    """Catalogued angular size (SIMBAD ``galdim_*``).

    Attributes:
        major_arcmin, minor_arcmin: Full axis lengths (diameters), arcmin.
        position_angle_deg: Major-axis position angle, degrees east of north.
        quality: SIMBAD dimension quality letter (A best .. E worst), if given.
        shape: ``EXTENT_*`` describing what can be drawn reliably.
    """

    major_arcmin: float | None = None
    minor_arcmin: float | None = None
    position_angle_deg: float | None = None
    quality: str | None = None
    shape: str = EXTENT_NONE

    @property
    def has_size(self) -> bool:
        return self.shape != EXTENT_NONE

    @property
    def semi_major_deg(self) -> float:
        return (self.major_arcmin or 0.0) / 120.0

    @property
    def semi_minor_deg(self) -> float:
        if self.shape == EXTENT_ELLIPSE and self.minor_arcmin:
            return self.minor_arcmin / 120.0
        return self.semi_major_deg

    @property
    def drawn_position_angle_deg(self) -> float:
        return self.position_angle_deg if self.shape == EXTENT_ELLIPSE else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObjectAssociation:
    """Image evidence for one retained object (objective measurements only).

    Attributes:
        kind: ``ASSOC_*``.
        detection_source_id: Milestone 2 ``source_id`` of the associated detection.
        separation_px, separation_arcsec: Object position to that detection.
        n_detections_within_radius: Accepted detections within the association radius.
        detections_in_footprint: Accepted detections inside the catalogued footprint.
        footprint_median, annulus_median, annulus_sigma: Detection-plane statistics inside
            the footprint and in a surrounding annulus (1.5-2.5x the footprint); sigma is
            1.4826 x MAD.
        brightness_contrast: ``(footprint_median - annulus_median) / annulus_sigma``.
        note: Why a measurement is absent, if it is.
    """

    kind: str
    detection_source_id: int | None = None
    separation_px: float | None = None
    separation_arcsec: float | None = None
    n_detections_within_radius: int = 0
    detections_in_footprint: int | None = None
    footprint_pixels: int | None = None
    footprint_median: float | None = None
    annulus_median: float | None = None
    annulus_sigma: float | None = None
    brightness_contrast: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CatalogObject:
    """One catalogue row, projected and classified.

    ``main_id``/``catalogue_id`` keep the raw catalogue identity; ``display_name`` is the
    preferred designation (``objects.naming``). ``projected_x/y`` are canonical pixels (NaN if
    the WCS cannot project the position).
    """

    catalogue: str
    catalogue_id: int
    main_id: str
    display_name: str
    common_names: tuple[str, ...]
    aliases: tuple[str, ...]
    object_type: str
    object_type_description: str | None
    object_type_path: str | None
    category: str
    candidate_type: bool
    ra_deg: float
    dec_deg: float
    projected_x: float
    projected_y: float
    field_status: str
    centre_in_image: bool
    footprint_intersects_image: bool
    footprint_fraction_in_image: float | None
    extent: ObjectExtent
    magnitudes: dict[str, float]
    redshift: float | None
    morphological_type: str | None
    n_references: int | None
    status: str
    exclusion_reason: str | None = None
    association: ObjectAssociation | None = None
    footprint_px: tuple[tuple[float, float], ...] | None = None  # outline, for drawing
    name_rank: int = 99

    @property
    def retained(self) -> bool:
        return self.status == STATUS_IN_FIELD

    def to_dict(self, *, include_footprint: bool = False) -> dict[str, Any]:
        record = asdict(self)
        record["aliases"] = list(self.aliases)
        record["common_names"] = list(self.common_names)
        record["retained"] = self.retained
        if not include_footprint:
            record.pop("footprint_px")
        elif self.footprint_px is not None:
            record["footprint_px"] = [list(p) for p in self.footprint_px]
        return record


@dataclass(frozen=True, eq=False)
class ObjectQueryResult:
    """A named-object catalogue response plus everything needed to reproduce it."""

    service: str
    service_url: str
    table: str
    region: QueryRegion
    outer_radius_deg: float
    adql: str
    columns: tuple[str, ...]
    row_limit: int
    rows: tuple[dict[str, Any], ...]
    origin: str  # "live" or "cache"
    queried_at: str
    query_seconds: float | None
    cache_key: str
    cache_path: str | None = None
    raw_response: bytes = b""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "service_url": self.service_url,
            "table": self.table,
            "region": self.region.to_dict(),
            "outer_radius_deg": self.outer_radius_deg,
            "extended_object_rule": "rows outside the cone are kept when their catalogued "
            "semi-major axis reaches into it (up to the outer radius)",
            "adql": self.adql,
            "columns": list(self.columns),
            "filters": "none on object type at query time (types are filtered locally)",
            "row_limit": self.row_limit,
            "rows_returned": len(self.rows),
            "origin": self.origin,
            "queried_at": self.queried_at,
            "query_seconds": self.query_seconds,
            "cache_key": self.cache_key,
            "cache_path": self.cache_path,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class WcsChoice:
    """Which WCS was used and where it came from."""

    path: str
    refined: bool | None  # None: explicit file of unknown provenance
    source: str  # "explicit", "catalog_refined" or "plate_solution"
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class IdentificationResult:
    """Everything produced by object identification."""

    query: ObjectQueryResult
    objects: tuple[CatalogObject, ...]  # every returned row, listing order
    wcs: WcsChoice | None
    image_width: int
    image_height: int
    pixel_scale_arcsec: float
    n_detections: int | None
    included_categories: tuple[str, ...]
    runtime_seconds: float
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def retained(self) -> tuple[CatalogObject, ...]:
        return tuple(o for o in self.objects if o.retained)

    @property
    def in_field(self) -> tuple[CatalogObject, ...]:
        return tuple(o for o in self.objects if o.status != STATUS_OUTSIDE)

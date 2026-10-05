"""Versioned data model of Milestone 6 evidence assessments.

An assessment answers: *how well do the existing measurements support the statement "this
catalogued object is at this place in the image"?* It is an ordinal **support level** with
reason codes, never a probability. No numeric score is produced (``evidence_score`` stays
``None``; see the README).

Missing information is ``None`` plus a ``MISSING_*`` reason code. It is never converted
into zero or negative evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Bump when fields or rules change meaning.
EVIDENCE_VERSION = "6.0"

# Support levels, strongest first. ``catalogue-only``: the catalogue and WCS place the
# object here but the image adds no support (not detected, or not measurable).
# ``insufficient``: abstention (prerequisites missing, object mostly off-frame, severe
# ambiguity, or nothing supports it under a poor WCS).
LEVEL_STRONG = "strong"
LEVEL_MODERATE = "moderate"
LEVEL_WEAK = "weak"
LEVEL_CATALOGUE_ONLY = "catalogue-only"
LEVEL_INSUFFICIENT = "insufficient"
SUPPORT_LEVELS: tuple[str, ...] = (
    LEVEL_STRONG,
    LEVEL_MODERATE,
    LEVEL_WEAK,
    LEVEL_CATALOGUE_ONLY,
    LEVEL_INSUFFICIENT,
)

# Component grades.
GRADE_STRONG = "strong"
GRADE_MODERATE = "moderate"
GRADE_WEAK = "weak"
GRADE_NONE = "none"  # measured, and the image shows nothing supporting
GRADE_UNAVAILABLE = "unavailable"  # could not be measured (missing, not negative)

# Field astrometry grades.
ASTROMETRY_PRECISE = "precise"
ASTROMETRY_ADEQUATE = "adequate"
ASTROMETRY_POOR = "poor"
ASTROMETRY_UNAVAILABLE = "unavailable"

PATH_COMPACT = "compact"
PATH_EXTENDED = "extended"


@dataclass(frozen=True)
class AstrometryEvidence:
    """Quality of the WCS that placed the object, and the positional scale near it.

    ``r50_px`` is the median radial Gaia residual (half of all matched stars lie closer):
    local (nearest matches) when stable, otherwise field-level. ``r50_source`` says which.
    """

    grade: str
    wcs_source: str | None
    wcs_refined: bool | None
    plate_solution_mode: str | None
    gaia_matches: int | None
    gaia_match_fraction: float | None
    gaia_median_residual_px: float | None
    gaia_rms_residual_px: float | None
    gaia_median_residual_arcsec: float | None
    plate_solver_median_residual_arcsec: float | None
    plate_solver_matches: int | None
    epoch_propagated: bool | None
    r50_px: float | None
    r50_arcsec: float | None
    r50_source: str | None  # "local_gaia", "field_gaia", "input_wcs_offset", "plate_solver"
    n_local_matches: int | None
    local_radius_px: float | None


@dataclass(frozen=True)
class CatalogueEvidence:
    """Catalogue facts. Popularity priors (designation rank, references) are not scored."""

    catalogue: str
    catalogue_id: int
    main_id: str
    display_name: str
    aliases_count: int
    object_type: str
    object_type_description: str | None
    category: str
    candidate_type: bool
    has_size: bool
    has_position_angle: bool
    major_axis_arcmin: float | None
    minor_axis_arcmin: float | None
    position_angle_deg: float | None
    metadata_present: tuple[str, ...]
    metadata_missing: tuple[str, ...]


@dataclass(frozen=True)
class CompactImageEvidence:
    """Point-source association of a compact object (positions in canonical pixels)."""

    detection_source_id: int | None
    separation_px: float | None
    separation_arcsec: float | None
    normalized_offset: float | None  # separation / r50
    offset_grade: str | None  # "close", "consistent", "poor" or None
    chance_expectation: float | None  # unrelated detections expected in the 3 r50 region
    source_snr: float | None
    source_saturated: bool | None
    source_edge: bool | None
    source_astrometric_method: str | None
    detection_gaia_source_id: int | None
    grade: str


@dataclass(frozen=True)
class ExtendedImageEvidence:
    """Footprint measurements of an extended object on the original pixels."""

    centre_in_image: bool
    visible_fraction: float | None
    visibility: str  # "mostly_visible", "truncated", "larger_than_frame", "mostly_outside"
    radius_px: float | None
    placement_ratio: float | None  # r50 / radius
    footprint_pixels: int | None
    annulus_pixels: int | None
    footprint_median: float | None
    annulus_median: float | None
    annulus_sigma: float | None
    contrast: float | None
    contrast_significance: float | None
    structure_pixels: int | None
    structure_offset_px: float | None
    structure_offset_fraction: float | None
    detections_in_footprint: int | None  # context only (foreground stars), not scored
    grade: str


@dataclass(frozen=True)
class Competitor:
    catalogue_id: int
    display_name: str
    object_type: str
    separation_px: float
    relation: str  # "competitor", "indistinguishable", "contains", "within"


@dataclass(frozen=True)
class AmbiguityEvidence:
    severity: str  # "none", "minor", "major", "severe"
    competitors: tuple[Competitor, ...] = ()
    related: tuple[Competitor, ...] = ()  # nested objects (first 20): context, not competition
    n_related: int = 0


@dataclass(frozen=True)
class ObjectEvidence:
    """The assessment of one retained Milestone 5 object."""

    catalogue_id: int
    display_name: str
    object_type: str
    category: str
    evidence_version: str
    path: str  # PATH_COMPACT or PATH_EXTENDED
    projected_x: float
    projected_y: float
    astrometry: AstrometryEvidence
    catalogue: CatalogueEvidence
    compact: CompactImageEvidence | None
    extended: ExtendedImageEvidence | None
    ambiguity: AmbiguityEvidence
    image_grade: str
    data_quality_flags: tuple[str, ...]
    missing: tuple[str, ...]
    caps: tuple[str, ...]  # rules that limited the level, in application order
    support_level: str
    reason_codes: tuple[str, ...]
    explanation: str
    evidence_score: float | None = None  # deliberately not produced: no calibrated score
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class EvidenceResult:
    """All assessments for one image."""

    objects: tuple[ObjectEvidence, ...]
    image_width: int
    image_height: int
    pixel_scale_arcsec: float
    field_astrometry: AstrometryEvidence
    config: Any
    provenance: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def counts(self) -> dict[str, int]:
        return {
            level: sum(o.support_level == level for o in self.objects) for level in SUPPORT_LEVELS
        }

"""Data models for Milestone 3 plate solving.

All pixel coordinates here use AstroIdentify's canonical convention (see
``astroidentify.detection.types.COORDINATE_CONVENTION``): 0-based array coordinates, x to
the right, y down, integer values at pixel centres. Conversion to the solver's convention
happens only in ``astrometry.xylist``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astropy.io.fits import Header
    from astropy.wcs import WCS

# Selection tiers, best first.
TIER_PREFERRED = "preferred"  # unsaturated, not edge-flagged, DAOFIND centroid
TIER_SECONDARY = "secondary"  # unsaturated, not edge-flagged, peak-search centroid
TIER_SATURATED = "saturated"  # saturated (core-centroided), not edge-flagged
TIER_EDGE = "edge"  # edge-flagged (any saturation state)

MODE_BLIND = "blind"
MODE_SCALE_CONSTRAINED = "scale-constrained"


@dataclass(frozen=True)
class SelectedSource:
    """A detection chosen for plate solving.

    Attributes:
        rank: 1-based position in the solver source list (brightest first).
        source_id: Milestone 2 ``source_id``.
        x, y: Canonical pixel coordinates (unchanged from detection).
        flux: Milestone 2 aperture flux (brightness ranking).
        snr: Milestone 2 SNR.
        saturated, edge: Milestone 2 flags.
        tier: Quality tier (``TIER_*``).
        cell: ``(column, row)`` of the spatial-balancing grid cell.
    """

    rank: int
    source_id: int
    x: float
    y: float
    flux: float
    snr: float
    saturated: bool
    edge: bool
    tier: str
    cell: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["cell"] = list(self.cell)
        return record


@dataclass(frozen=True)
class SourceSelection:
    """The source set handed to the solver, plus how it was chosen."""

    sources: tuple[SelectedSource, ...]
    image_width: int
    image_height: int
    grid_shape: tuple[int, int]
    allowed_tiers: tuple[str, ...]
    max_sources: int
    n_candidates_by_tier: dict[str, int]
    warnings: tuple[str, ...] = ()
    tier_mode: str = "sequential"

    def __len__(self) -> int:
        return len(self.sources)

    @property
    def n_by_tier(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for source in self.sources:
            counts[source.tier] = counts.get(source.tier, 0) + 1
        return counts

    @property
    def occupied_cells(self) -> int:
        return len({s.cell for s in self.sources})

    def signature(self) -> tuple[int, ...]:
        """Identifies the source set (used to skip redundant solver attempts)."""
        return tuple(s.source_id for s in self.sources)


@dataclass(frozen=True)
class SolverRun:
    """Raw outcome of one solver process."""

    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    runtime_seconds: float
    solved: bool
    wcs_path: Path | None
    corr_path: Path | None
    match_path: Path | None
    version: str | None = None


@dataclass(frozen=True)
class SolveAttempt:
    """One entry in the deterministic attempt sequence."""

    number: int
    name: str
    mode: str
    n_sources: int
    allowed_tiers: tuple[str, ...]
    scale_bounds_arcsec: tuple[float, float] | None
    solved: bool
    runtime_seconds: float | None
    command: tuple[str, ...] | None
    error: str | None = None
    skipped_reason: str | None = None
    status: str = "unsolved"
    timeout_seconds: float | None = None
    tier_mode: str = "sequential"

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["command"] = list(self.command) if self.command else None
        return record


@dataclass(frozen=True)
class SkyPosition:
    """A celestial position in degrees (ICRS as given by the WCS)."""

    ra_deg: float
    dec_deg: float

    def to_dict(self) -> dict[str, Any]:
        return {"ra_deg": self.ra_deg, "dec_deg": self.dec_deg}


@dataclass(frozen=True)
class WcsGeometry:
    """Geometry derived from a WCS for an image of known size.

    Attributes:
        centre: Sky position of the image centre, canonical pixel ``((W-1)/2, (H-1)/2)``.
        corners: Sky positions of the outer image corners (pixel edges), keyed
            ``top_left``, ``top_right``, ``bottom_right``, ``bottom_left`` (row 0 = top).
        pixel_scale_arcsec: Mean pixel scale at the image centre.
        pixel_scale_x_arcsec, pixel_scale_y_arcsec: Scale along the x and y pixel axes.
        field_width_deg, field_height_deg: Angular distance between the midpoints of the
            left/right and top/bottom image edges.
        up_position_angle_deg: Position angle (east of north, 0-360) of the image's up
            direction as displayed (towards row 0).
        parity: ``"normal"`` if the image, displayed with row 0 at the top, shows the sky
            as seen from the ground (east 90 degrees counter-clockwise from north) or
            ``"mirrored"``.
    """

    centre: SkyPosition
    corners: dict[str, SkyPosition]
    pixel_scale_arcsec: float
    pixel_scale_x_arcsec: float
    pixel_scale_y_arcsec: float
    field_width_deg: float
    field_height_deg: float
    up_position_angle_deg: float
    parity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "centre": self.centre.to_dict(),
            "corners": {name: pos.to_dict() for name, pos in self.corners.items()},
            "pixel_scale_arcsec": self.pixel_scale_arcsec,
            "pixel_scale_x_arcsec": self.pixel_scale_x_arcsec,
            "pixel_scale_y_arcsec": self.pixel_scale_y_arcsec,
            "field_width_deg": self.field_width_deg,
            "field_height_deg": self.field_height_deg,
            "field_width_arcmin": self.field_width_deg * 60.0,
            "field_height_arcmin": self.field_height_deg * 60.0,
            "up_position_angle_deg": self.up_position_angle_deg,
            "parity": self.parity,
        }


@dataclass(frozen=True)
class Correspondence:
    """A selected source matched to a solver index star (canonical pixel coordinates)."""

    source_id: int | None
    field_x: float
    field_y: float
    index_x: float
    index_y: float
    field_ra_deg: float
    field_dec_deg: float
    index_ra_deg: float
    index_dec_deg: float
    residual_px: float
    residual_arcsec: float
    match_weight: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchStatistics:
    """Raw astrometric match evidence (no derived 'confidence')."""

    n_matched: int
    n_selected: int
    match_fraction: float | None
    median_residual_arcsec: float | None
    rms_residual_arcsec: float | None
    max_residual_arcsec: float | None
    median_residual_px: float | None
    rms_residual_px: float | None
    solver_log_odds: float | None = None
    solver_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class PlateSolution:
    """Everything known about a plate-solving run, solved or not.

    Values that could not be derived are ``None``; nothing is fabricated.
    """

    solved: bool
    status: str
    backend: str
    backend_version: str | None
    mode: str | None
    constraints: dict[str, Any]
    selection: SourceSelection | None
    attempts: tuple[SolveAttempt, ...]
    solved_attempt: int | None
    geometry: WcsGeometry | None
    wcs: WCS | None
    wcs_header: Header | None
    correspondences: tuple[Correspondence, ...]
    match_statistics: MatchStatistics | None
    solver_report: dict[str, Any]
    runtime_seconds: float
    solver_log: str
    warnings: tuple[str, ...] = ()
    error: str | None = None
    exception: Exception | None = None

    def raise_for_status(self) -> None:
        """Raise the stored domain error if the field was not solved."""
        if not self.solved and self.exception is not None:
            raise self.exception

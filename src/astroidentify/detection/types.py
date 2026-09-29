"""Data models for Milestone 2 source detection.

Coordinate convention (all detection outputs):

* ``x`` is the column index (increases left -> right), ``y`` the row index (increases
  top -> bottom), both in the image array. Row 0 is displayed at the top.
* Integer coordinates are pixel centres: pixel ``[row, col]`` spans ``col - 0.5 .. col + 0.5``
  in x (the photutils/astropy 0-based convention).
* No flipping, resizing or cropping happens between measurement and the overlay. For FITS
  input this means the overlay shows row 0 at the top, unlike the Milestone 1 preview.

Units: fluxes, peaks, backgrounds and noise are in detection-plane units, i.e. the source's
own units (FITS values or raster code values), averaged over channels for colour images.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from astroidentify.config import DetectionConfig
    from astroidentify.types import PreprocessingResult

COORDINATE_CONVENTION: dict[str, str] = {
    "origin": "image array; x = column index, y = row index (0-based)",
    "x": "increases left to right",
    "y": "increases top to bottom",
    "pixel_centers": "integer coordinates are pixel centres; pixel (0, 0) spans -0.5..0.5",
    "row_zero_displayed_at": "top",
}


@dataclass(frozen=True, eq=False)
class DetectionPlane:
    """The 2-D image that detection runs on.

    Attributes:
        data: Read-only float32 ``(H, W)`` array, all finite.
        invalid_mask: Read-only bool ``(H, W)``; ``True`` where the source pixel was invalid
            (photutils mask convention). Those pixels hold ``fill_value`` in ``data``.
        method: How the plane was derived (``"identity"`` or ``"channel_mean"``).
        fill_value: Value written into invalid pixels (the Milestone 1 global background).
    """

    data: np.ndarray
    invalid_mask: np.ndarray
    method: str
    fill_value: float

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    @property
    def has_invalid(self) -> bool:
        return bool(self.invalid_mask.any())


@dataclass(frozen=True, eq=False)
class LocalBackground:
    """Spatially varying background model of a detection plane.

    Attributes:
        background: Read-only float32 ``(H, W)`` background map.
        rms: Read-only float32 ``(H, W)`` background RMS map (floored at ``rms_floor``).
        subtracted: Read-only float32 ``(H, W)`` detection plane minus ``background``.
        box_size: Tile size ``(ny, nx)`` actually used.
        filter_size: Tile-grid median filter size actually used.
        rms_floor: Lower limit applied to the RMS map (see ``detection.background``).
        warnings: Warnings raised while modelling the background.
    """

    background: np.ndarray
    rms: np.ndarray
    subtracted: np.ndarray
    box_size: tuple[int, int]
    filter_size: int
    rms_floor: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class Candidates:
    """Raw detections before measurement (parallel arrays, one entry per candidate).

    Attributes:
        x, y: Centroids in pixels.
        peak: Peak background-subtracted value.
        flux: Detector-level brightness used only to rank candidates internally.
        sharpness, roundness1, roundness2: DAOFIND statistics (NaN if not measured).
        method: Centroid method per candidate (``"daofind"`` or ``"peak_com"``).
    """

    x: np.ndarray
    y: np.ndarray
    peak: np.ndarray
    flux: np.ndarray
    sharpness: np.ndarray
    roundness1: np.ndarray
    roundness2: np.ndarray
    method: list[str]

    def __len__(self) -> int:
        return len(self.x)

    @property
    def xy(self) -> np.ndarray:
        return np.column_stack([self.x, self.y]).astype(float).reshape(-1, 2)

    @classmethod
    def empty(cls) -> Candidates:
        nothing = np.empty(0)
        return cls(nothing, nothing, nothing, nothing, nothing, nothing, nothing, [])

    @classmethod
    def concatenate(cls, first: Candidates, second: Candidates) -> Candidates:
        def join(name: str) -> np.ndarray:
            return np.concatenate([getattr(first, name), getattr(second, name)])

        return cls(
            *(join(n) for n in ("x", "y", "peak", "flux", "sharpness", "roundness1", "roundness2")),
            method=[*first.method, *second.method],
        )


@dataclass(frozen=True)
class FwhmEstimate:
    """The stellar FWHM used for detection and how it was obtained.

    Attributes:
        value: FWHM in pixels.
        method: ``"configured"``, ``"estimated"`` (median of Gaussian fits to bright
            unsaturated stars) or ``"initial_guess"`` (estimation failed; see warnings).
        n_stars: Number of stars in the final estimate (0 unless estimated).
        iterations: Number of fit iterations.
        spread: 16th-84th percentile range of the fitted values, or ``None``.
    """

    value: float
    method: str
    n_stars: int = 0
    iterations: int = 0
    spread: tuple[float, float] | None = None


@dataclass(frozen=True)
class Source:
    """One detected source candidate. See ``SOURCE_FIELDS`` for column definitions."""

    source_id: int
    x: float
    y: float
    flux: float
    flux_err: float
    snr: float
    peak: float
    fwhm: float
    sharpness: float
    roundness1: float
    roundness2: float
    local_background: float
    local_rms: float
    edge_distance: float
    n_saturated_pixels: int
    saturated: bool
    edge: bool
    centroid_method: str = "daofind"
    saturated_core_axis_ratio: float = float("nan")
    saturated_core_area: int = 0
    duplicate_of: int | None = None
    accepted: bool = True
    rejection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["rejection_reasons"] = list(self.rejection_reasons)
        return record


#: Column descriptions, exported with the source tables.
SOURCE_FIELDS: dict[str, str] = {
    "source_id": "1-based ID in order of decreasing flux (1 = brightest candidate)",
    "x": "centroid column in pixels (see centroid_method)",
    "y": "centroid row in pixels (see centroid_method)",
    "flux": "background-subtracted sum in a circular aperture of radius aperture_radius",
    "flux_err": "background-limited error: sqrt(sum of RMS-map^2 over the aperture) times "
    "the empirical correlated-noise factor",
    "snr": "flux / flux_err",
    "peak": "peak background-subtracted pixel value near the source",
    "fwhm": "effective FWHM = 2.3548 * sqrt(flux / (2 pi peak)), i.e. the FWHM of a "
    "circular Gaussian with the same flux and peak; inflated for saturated sources",
    "sharpness": "DAOFIND sharpness (about 0.2-1 for stars; low = extended, high = hot "
    "pixel); empty for peak_com candidates",
    "roundness1": "DAOFIND symmetry-based roundness (0 = round); empty for peak_com candidates",
    "roundness2": "DAOFIND marginal-fit roundness (0 = round); empty for peak_com candidates",
    "local_background": "local background map value at the centroid",
    "local_rms": "local background RMS map value at the centroid",
    "edge_distance": "distance from the centroid to the nearest image border, pixels",
    "n_saturated_pixels": "pixels in the aperture with any channel at or above saturation",
    "saturated": "n_saturated_pixels > 0",
    "edge": "edge_distance < edge_flag_fwhm * FWHM",
    "centroid_method": "'daofind' (marginal Gaussian fits), 'peak_com' (centre of mass around "
    "a smoothed-image peak that DAOFIND could not fit) or 'saturated_core' (centroid of the "
    "connected saturated region the source sits on)",
    "saturated_core_axis_ratio": "minor/major axis ratio of the saturated region the source "
    "sits on (second moments); empty if not on a saturated core",
    "saturated_core_area": "pixel count of that saturated region (0 if none)",
    "duplicate_of": "source_id of the primary detection on the same saturated core, else null",
    "accepted": "passed all filters",
    "rejection_reasons": "filters the candidate failed (empty if accepted)",
}


@dataclass(frozen=True, eq=False)
class DetectionResult:
    """Everything produced by source detection.

    ``sources`` holds every raw candidate (accepted and rejected), ordered by decreasing
    flux so ``source_id`` 1 is the brightest.
    """

    preprocessing: PreprocessingResult
    plane: DetectionPlane
    background: LocalBackground
    sources: tuple[Source, ...]
    fwhm: FwhmEstimate
    aperture_radius: float
    noise_factor: float
    saturation_level: float | None
    saturation_source: str
    config: DetectionConfig
    diagnostics: dict[str, Any]

    @property
    def accepted_sources(self) -> tuple[Source, ...]:
        """Accepted sources, brightest first."""
        return tuple(s for s in self.sources if s.accepted)

    @property
    def rejected_sources(self) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if not s.accepted)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self.diagnostics.get("warnings", ()))

    def brightest(self, n: int | None = None, *, accepted_only: bool = True) -> list[Source]:
        """Return up to ``n`` sources sorted by decreasing flux."""
        pool = self.accepted_sources if accepted_only else self.sources
        ranked = sorted(pool, key=brightness_key)
        return ranked if n is None else ranked[:n]

    def xy_flux(self, *, accepted_only: bool = True) -> np.ndarray:
        """``(N, 3)`` array of ``x, y, flux``, brightest first (plate-solver input)."""
        rows = [(s.x, s.y, s.flux) for s in self.brightest(accepted_only=accepted_only)]
        return np.array(rows, dtype=np.float64).reshape(-1, 3)


def brightness_key(source: Source) -> tuple[float, float, float]:
    """Sort key: decreasing flux, ties broken by position (deterministic)."""
    flux = source.flux if np.isfinite(source.flux) else -np.inf
    return (-flux, source.y, source.x)

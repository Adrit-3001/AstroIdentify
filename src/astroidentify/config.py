"""Centralized, explicit configuration for all pipeline stages."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from astroidentify.exceptions import ConfigurationError

PreviewStretch = Literal["auto", "linear", "asinh"]
PREVIEW_STRETCHES: tuple[str, ...] = ("auto", "linear", "asinh")


@dataclass(frozen=True)
class PreprocessingConfig:
    """All tunable values used by Milestone 1 preprocessing.

    Attributes:
        fits_hdu: Index of the FITS HDU to use. ``None`` selects the first HDU containing
            usable 2-D image data (primary HDU first, then extensions in file order).
        min_dimension: Minimum accepted width and height in pixels. Rejects degenerate
            inputs such as 1-row FITS spectra that are technically 2-D.
        background_clip_sigma: Sigma-clipping threshold used to reject stars and other
            bright/dark outliers before measuring the background. ``None`` disables
            clipping (plain median / MAD over all finite pixels).
        background_clip_maxiters: Maximum number of sigma-clipping iterations.
        normalization_lower_percentile: Percentile of finite pixel values mapped to 0.0.
        normalization_upper_percentile: Percentile of finite pixel values mapped to 1.0.
        normalization_clip: Clip normalized values to [0, 1]. Off by default so that faint
            signal below the lower percentile and bright cores above the upper percentile
            are preserved.
        preview_lower_percentile: Display black point for the preview.
        preview_upper_percentile: Display white point for the preview.
        preview_stretch: ``"linear"``, ``"asinh"``, or ``"auto"`` (asinh for FITS, which is
            usually linear sensor data; linear for JPEG/PNG, which are usually already
            display-encoded).
        preview_asinh_softening: Softening parameter ``a`` of the asinh stretch
            ``asinh(x / a) / asinh(1 / a)``; smaller values brighten faint structure more.
        preview_max_dimension: If set, downscale the preview so its longest side is at most
            this many pixels (aspect ratio preserved). ``None`` keeps full resolution.
    """

    fits_hdu: int | None = None
    min_dimension: int = 8

    background_clip_sigma: float | None = 3.0
    background_clip_maxiters: int = 5

    normalization_lower_percentile: float = 1.0
    normalization_upper_percentile: float = 99.5
    normalization_clip: bool = False

    preview_lower_percentile: float = 0.5
    preview_upper_percentile: float = 99.8
    preview_stretch: PreviewStretch = "auto"
    preview_asinh_softening: float = 0.1
    preview_max_dimension: int | None = None

    def __post_init__(self) -> None:
        if self.fits_hdu is not None and self.fits_hdu < 0:
            raise ConfigurationError(f"fits_hdu must be >= 0, got {self.fits_hdu}")
        if self.min_dimension < 1:
            raise ConfigurationError(f"min_dimension must be >= 1, got {self.min_dimension}")
        if self.background_clip_sigma is not None and self.background_clip_sigma <= 0:
            raise ConfigurationError(
                f"background_clip_sigma must be positive or None, got {self.background_clip_sigma}"
            )
        if self.background_clip_maxiters < 1:
            raise ConfigurationError(
                f"background_clip_maxiters must be >= 1, got {self.background_clip_maxiters}"
            )
        _check_percentiles(
            "normalization",
            self.normalization_lower_percentile,
            self.normalization_upper_percentile,
        )
        _check_percentiles("preview", self.preview_lower_percentile, self.preview_upper_percentile)
        if self.preview_stretch not in PREVIEW_STRETCHES:
            raise ConfigurationError(
                f"preview_stretch must be one of {PREVIEW_STRETCHES}, got {self.preview_stretch!r}"
            )
        if self.preview_asinh_softening <= 0:
            raise ConfigurationError(
                f"preview_asinh_softening must be positive, got {self.preview_asinh_softening}"
            )
        if self.preview_max_dimension is not None and self.preview_max_dimension < 1:
            raise ConfigurationError(
                f"preview_max_dimension must be >= 1 or None, got {self.preview_max_dimension}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the configuration."""
        return asdict(self)


@dataclass(frozen=True)
class DetectionConfig:
    """All tunable values used by Milestone 2 source detection.

    Lengths given in units of FWHM scale with the (configured or estimated) stellar FWHM,
    so the defaults carry over between cameras and plate scales.

    Attributes:
        background_box_size: Side of the square tiles (pixels) used for the local background
            map. Should be several times larger than stars but small enough to follow
            gradients. Clamped to the image size for small images.
        background_filter_size: Median filter size (in tiles) applied to the tile grid,
            suppressing tiles biased by bright stars or extended objects. 1 disables it.
        background_clip_sigma: Sigma-clipping threshold inside each tile.
        background_exclude_percentile: Tiles with more than this percentage of masked
            (invalid) pixels are excluded and interpolated over.
        detection_sigma: Detection threshold as a multiple of the local background RMS.
        fwhm: Stellar FWHM in pixels. ``None`` estimates it from bright unsaturated stars.
        fwhm_initial_guess: Starting FWHM (pixels) for the automatic estimate.
        fwhm_min: Lower bound (pixels) for the automatic estimate.
        fwhm_max: Upper bound (pixels) for the automatic estimate.
        fwhm_estimation_sigma: Detection threshold (in RMS) used to pick the bright stars
            that the FWHM is estimated from.
        fwhm_estimation_max_stars: Maximum number of stars used for the FWHM estimate.
        fwhm_estimation_min_stars: Minimum usable stars; otherwise fall back to the guess.
        min_separation_fwhm: Minimum separation between detections, in FWHM.
        aperture_radius_fwhm: Photometry aperture radius in FWHM. 1.0 captures ~94% of a
            Gaussian's flux while keeping background noise in the aperture moderate.
        saturation_level: Pixel value (source units) at which a pixel counts as saturated.
            ``None`` uses the source metadata (raster nominal maximum, FITS ``SATURATE``).
        edge_flag_fwhm: Sources whose centroid is closer than this (in FWHM) to the image
            border are flagged as edge sources (not rejected for that alone).
        edge_reject_fwhm: Sources closer than this (in FWHM) to the border are rejected as
            ``too_close_to_edge``: the stellar core itself is cut off, biasing the centroid.
        min_snr: Candidates with aperture SNR below this are rejected (``low_snr``).
        sharpness_range: Inclusive DAOFIND sharpness bounds; outside -> ``not_star_like``.
            Saturated sources are exempt, because clipped cores distort the statistic.
        max_abs_roundness: Candidates with ``|roundness1|`` or ``|roundness2|`` above this are
            rejected as ``too_elongated`` (saturated sources exempt). 1.0 only removes extreme
            cases such as line-like artifacts.
        min_saturated_axis_ratio: Saturated sources whose saturated region has a minor/major
            axis ratio below this (more elongated than 2:1 by default) are rejected as
            ``elongated_saturated_region``.
        saturated_shape_min_diameter_fwhm: The saturated-region shape rule only applies to
            regions whose equivalent-circle diameter is at least this many FWHM; smaller
            regions are too pixelized for their shape to mean anything.
        max_fwhm_ratio: Candidates whose effective FWHM exceeds this multiple of the median
            effective FWHM of the stars are rejected as ``extended`` (saturated sources
            exempt).
        correct_correlated_noise: Scale aperture errors by the empirically measured
            "empty aperture" noise (accounts for spatially correlated noise).
        empty_aperture_min_count: Minimum number of source-free apertures needed to measure
            the correlated-noise factor; otherwise no correction is applied.
        astrometric_centroid: Compute isophote-calibrated astrometric centroids for
            saturated cores (``detection.astrometric_centroid``). ``False`` makes the
            astrometric centroid equal to the detection centroid for every source.
        isophote_calibration_max_stars: Maximum unsaturated stars stacked for the
            isophote calibration (the brightest suitable ones).
        isophote_calibration_min_stars: Fewer suitable stars -> no calibration; saturated
            cores keep their plateau centroid (``saturated_core_fallback``).
        isophote_stack_half_width_fwhm: Half-size of the stacked-PSF cutout, in FWHM. Bounds
            the largest isophote (and so the largest saturated core) that can be corrected.
        isophote_isolation_fwhm: Stacked stars have no other accepted detection within this
            many FWHM.
        overlay_max_labels: Label this many of the brightest accepted sources with their ID.
    """

    background_box_size: int = 64
    background_filter_size: int = 3
    background_clip_sigma: float = 3.0
    background_exclude_percentile: float = 10.0

    detection_sigma: float = 5.0
    fwhm: float | None = None
    fwhm_initial_guess: float = 4.0
    fwhm_min: float = 1.0
    fwhm_max: float = 30.0
    fwhm_estimation_sigma: float = 20.0
    fwhm_estimation_max_stars: int = 50
    fwhm_estimation_min_stars: int = 5
    min_separation_fwhm: float = 2.5

    aperture_radius_fwhm: float = 1.0
    saturation_level: float | None = None
    edge_flag_fwhm: float = 2.0
    edge_reject_fwhm: float = 1.0

    min_snr: float = 5.0
    sharpness_range: tuple[float, float] = (0.2, 1.0)
    max_abs_roundness: float = 1.0
    min_saturated_axis_ratio: float = 0.5
    saturated_shape_min_diameter_fwhm: float = 1.0
    max_fwhm_ratio: float = 2.0
    correct_correlated_noise: bool = True
    empty_aperture_min_count: int = 30

    astrometric_centroid: bool = True
    isophote_calibration_max_stars: int = 150
    isophote_calibration_min_stars: int = 10
    isophote_stack_half_width_fwhm: float = 5.0
    isophote_isolation_fwhm: float = 4.0

    overlay_max_labels: int = 100

    def __post_init__(self) -> None:
        positive = {
            "detection_sigma": self.detection_sigma,
            "fwhm_initial_guess": self.fwhm_initial_guess,
            "fwhm_min": self.fwhm_min,
            "fwhm_estimation_sigma": self.fwhm_estimation_sigma,
            "aperture_radius_fwhm": self.aperture_radius_fwhm,
            "background_clip_sigma": self.background_clip_sigma,
            "max_abs_roundness": self.max_abs_roundness,
            "max_fwhm_ratio": self.max_fwhm_ratio,
            "isophote_stack_half_width_fwhm": self.isophote_stack_half_width_fwhm,
            "isophote_isolation_fwhm": self.isophote_isolation_fwhm,
        }
        for name, value in positive.items():
            if not value > 0:
                raise ConfigurationError(f"{name} must be positive, got {value}")
        if self.fwhm is not None and not self.fwhm > 0:
            raise ConfigurationError(f"fwhm must be positive or None, got {self.fwhm}")
        if not self.fwhm_min <= self.fwhm_initial_guess <= self.fwhm_max:
            raise ConfigurationError(
                "fwhm bounds must satisfy fwhm_min <= fwhm_initial_guess <= fwhm_max, got "
                f"{self.fwhm_min}, {self.fwhm_initial_guess}, {self.fwhm_max}"
            )
        if not 1 <= self.isophote_calibration_min_stars <= self.isophote_calibration_max_stars:
            raise ConfigurationError(
                "isophote calibration star counts must satisfy 1 <= min <= max, got "
                f"{self.isophote_calibration_min_stars}, {self.isophote_calibration_max_stars}"
            )
        if self.background_box_size < 4:
            raise ConfigurationError(
                f"background_box_size must be >= 4, got {self.background_box_size}"
            )
        if self.background_filter_size < 1 or self.background_filter_size % 2 == 0:
            raise ConfigurationError(
                f"background_filter_size must be a positive odd integer, "
                f"got {self.background_filter_size}"
            )
        if not 0 <= self.background_exclude_percentile <= 100:
            raise ConfigurationError(
                "background_exclude_percentile must be within [0, 100], "
                f"got {self.background_exclude_percentile}"
            )
        non_negative = {
            "min_separation_fwhm": self.min_separation_fwhm,
            "edge_flag_fwhm": self.edge_flag_fwhm,
            "edge_reject_fwhm": self.edge_reject_fwhm,
            "saturated_shape_min_diameter_fwhm": self.saturated_shape_min_diameter_fwhm,
            "min_snr": self.min_snr,
            "overlay_max_labels": self.overlay_max_labels,
        }
        for name, value in non_negative.items():
            if value < 0:
                raise ConfigurationError(f"{name} must be >= 0, got {value}")
        if self.empty_aperture_min_count < 1:
            raise ConfigurationError(
                f"empty_aperture_min_count must be >= 1, got {self.empty_aperture_min_count}"
            )
        if not 0 <= self.min_saturated_axis_ratio <= 1:
            raise ConfigurationError(
                "min_saturated_axis_ratio must be within [0, 1], "
                f"got {self.min_saturated_axis_ratio}"
            )
        if self.fwhm_estimation_max_stars < 1 or self.fwhm_estimation_min_stars < 1:
            raise ConfigurationError("fwhm estimation star counts must be >= 1")
        if self.saturation_level is not None and not math.isfinite(self.saturation_level):
            raise ConfigurationError(
                f"saturation_level must be finite, got {self.saturation_level}"
            )
        low, high = self.sharpness_range
        if not low < high:
            raise ConfigurationError(
                f"sharpness_range must be (low, high), got {self.sharpness_range}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the configuration."""
        return asdict(self)


@dataclass(frozen=True)
class AstrometryConfig:
    """All tunable values used by Milestone 3 plate solving.

    Nothing here may carry target knowledge: there is deliberately no RA/Dec or object-name
    setting. The only optional constraint is a generic pixel-scale range, which must come
    from telescope/camera properties and is used only by the final fallback attempt.

    Attributes:
        max_sources: Target number of sources in the first (preferred) selection.
        expanded_max_sources: Target number for the larger fallback selections.
        min_sources: Fewer usable sources than this is an error (the field cannot be solved).
        grid_cells: Approximate number of cells in the spatial-balancing grid; the grid shape
            follows the image aspect ratio. Ignored when ``grid_shape`` is set.
        grid_shape: Explicit ``(columns, rows)`` for the balancing grid, or ``None``.
        include_saturated: Rank saturated (core-centroided) sources together with
            unsaturated ones by brightness in the main attempts. Blind solvers match the
            brightest field stars to index stars, and in processed consumer images the
            brightest stars are usually saturated. ``False`` sends unsaturated sources only.
        allow_edge_fallback: Allow edge-flagged sources in the fallback attempt.
        solve_field_path: Path to ``solve-field``; ``None`` searches ``PATH``.
        astrometry_config: Astrometry.net engine config file; ``None`` uses the system
            default (``/etc/astrometry.cfg``). Ignored when ``index_dirs`` is set.
        index_dirs: Directories of index files; if given, a config listing only these is
            generated for the solver.
        timeout_seconds: Wall-clock limit for each solver attempt (hard kill).
        total_timeout_seconds: Wall-clock budget for the whole attempt sequence; a
            timed-out attempt does not stop later attempts while budget remains.
        cpulimit_seconds: CPU-time limit passed to the solver (``--cpulimit``), capped at
            the attempt's wall-clock limit.
        scale_low_arcsec: Optional lower pixel-scale bound (arcsec/pixel) from camera
            properties. When set, a scale-constrained attempt runs first; blind attempts
            follow if it fails.
        scale_high_arcsec: Optional upper pixel-scale bound (arcsec/pixel), as above.
        keep_temp: Keep the solver working directory (debugging).
        overlay_max_labels: Rank labels drawn on the selection overlay.
    """

    max_sources: int = 100
    expanded_max_sources: int = 200
    min_sources: int = 10
    grid_cells: int = 16
    grid_shape: tuple[int, int] | None = None
    include_saturated: bool = True
    allow_edge_fallback: bool = True

    solve_field_path: str | None = None
    astrometry_config: str | None = None
    index_dirs: tuple[str, ...] = ()
    timeout_seconds: float = 300.0
    total_timeout_seconds: float = 900.0
    cpulimit_seconds: float = 300.0
    scale_low_arcsec: float | None = None
    scale_high_arcsec: float | None = None
    keep_temp: bool = False

    overlay_max_labels: int = 30

    def __post_init__(self) -> None:
        if self.min_sources < 3:
            raise ConfigurationError(f"min_sources must be >= 3, got {self.min_sources}")
        if self.max_sources < self.min_sources:
            raise ConfigurationError(
                f"max_sources ({self.max_sources}) must be >= min_sources ({self.min_sources})"
            )
        if self.expanded_max_sources < self.max_sources:
            raise ConfigurationError(
                f"expanded_max_sources ({self.expanded_max_sources}) must be >= "
                f"max_sources ({self.max_sources})"
            )
        if self.grid_cells < 1:
            raise ConfigurationError(f"grid_cells must be >= 1, got {self.grid_cells}")
        if self.grid_shape is not None and min(self.grid_shape) < 1:
            raise ConfigurationError(f"grid_shape must be positive, got {self.grid_shape}")
        if not (
            self.timeout_seconds > 0
            and self.cpulimit_seconds > 0
            and self.total_timeout_seconds > 0
        ):
            raise ConfigurationError(
                "timeout_seconds, total_timeout_seconds and cpulimit_seconds must be positive"
            )
        scales = (self.scale_low_arcsec, self.scale_high_arcsec)
        if (scales[0] is None) != (scales[1] is None):
            raise ConfigurationError("scale_low_arcsec and scale_high_arcsec must be set together")
        if scales[0] is not None and not 0 < scales[0] < scales[1]:
            raise ConfigurationError(
                f"scale bounds must satisfy 0 < low < high, got {scales[0]}, {scales[1]}"
            )
        if self.overlay_max_labels < 0:
            raise ConfigurationError("overlay_max_labels must be >= 0")

    @property
    def scale_bounds(self) -> tuple[float, float] | None:
        if self.scale_low_arcsec is None or self.scale_high_arcsec is None:
            return None
        return (self.scale_low_arcsec, self.scale_high_arcsec)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the configuration."""
        return asdict(self)


GAIA_DR3_COLUMNS: tuple[str, ...] = (
    "source_id",
    "ra",
    "dec",
    "ra_error",
    "dec_error",
    "phot_g_mean_mag",
    "phot_bp_mean_mag",
    "phot_rp_mean_mag",
    "pmra",
    "pmdec",
    "parallax",
    "ref_epoch",
)


@dataclass(frozen=True)
class CatalogConfig:
    """All tunable values used by Milestone 4 catalogue matching.

    There is deliberately no sky-position or object-name setting: the query region is
    always derived from the solved WCS.

    Attributes:
        tap_url: Base URL of the Gaia TAP service (synchronous queries go to ``/sync``).
        table: Gaia DR3 source table.
        columns: Columns retrieved (compact: astrometry, photometry, motion).
        query_margin_arcsec: Safety margin added to the farthest-corner cone radius.
        row_limit: Maximum rows requested (TAP ``MAXREC``). Hitting it is an error, never
            silently treated as a complete field.
        network_timeout_seconds: Time limit for the catalogue request.
        cache_dir: Directory for the on-disk response cache; ``None`` disables caching.
        refresh_cache: Ignore an existing cache entry and query live (the entry is rewritten).
        match_radius_arcsec: Maximum detection-to-catalogue separation for a final match.
        brightness_rank_factor: Only the brightest ``factor * N_detections`` in-frame catalogue
            stars are eligible for matching (``None``: all). A catalogue far deeper than the
            image mostly adds faint stars that can only produce chance coincidences.
        refine_wcs: Refine the input WCS against the catalogue before the final match (see
            ``catalogs.matching``); the input WCS is never modified.
        registration_radius_arcsec: Generous radius for the registration pairs used to refine
            the WCS (must exceed the input WCS's systematic error).
        registration_isolation_ratio: A registration pair is kept only if the second-nearest
            eligible catalogue star is at least this many times farther than the nearest.
        refine_sip_degree: SIP distortion degree of the refined WCS (``None``: plain TAN).
        refine_min_pairs: Minimum registration pairs needed to refine; otherwise the input WCS
            is used and a warning is recorded.
        refine_clip_sigma: Registration pairs whose residual exceeds this many per-axis
            sigma (robust, from the Rayleigh-distributed residual magnitudes) are discarded
            and the fit repeated. 4 sigma discards ~0.03% of correct pairs.
        edge_margin_px: In-image filter margin: projected catalogue positions within this many
            pixels outside the image are kept (0 = exact image bounds).
        observation_epoch: Explicit observation date/time (ISO 8601, e.g. from an observing
            log) used for proper-motion propagation when the image metadata has none. Never
            inferred from a filename.
        overlay_max_labels: Matched stars labelled with their Gaia G magnitude on the overlay.
    """

    tap_url: str = "https://gea.esac.esa.int/tap-server/tap"
    table: str = "gaiadr3.gaia_source"
    columns: tuple[str, ...] = GAIA_DR3_COLUMNS
    query_margin_arcsec: float = 30.0
    row_limit: int = 200_000
    network_timeout_seconds: float = 120.0
    cache_dir: str | None = None
    refresh_cache: bool = False

    match_radius_arcsec: float = 3.0
    brightness_rank_factor: float | None = 3.0
    refine_wcs: bool = True
    registration_radius_arcsec: float = 12.0
    registration_isolation_ratio: float = 2.0
    refine_sip_degree: int | None = None
    refine_min_pairs: int = 20
    refine_clip_sigma: float = 4.0
    edge_margin_px: float = 0.0
    observation_epoch: str | None = None

    overlay_max_labels: int = 20

    def __post_init__(self) -> None:
        required = {"source_id", "ra", "dec"}
        if not required <= set(self.columns):
            raise ConfigurationError(f"columns must include {sorted(required)}")
        if self.query_margin_arcsec < 0:
            raise ConfigurationError("query_margin_arcsec must be >= 0")
        if self.row_limit < 1:
            raise ConfigurationError(f"row_limit must be >= 1, got {self.row_limit}")
        if not self.network_timeout_seconds > 0:
            raise ConfigurationError("network_timeout_seconds must be positive")
        if not self.match_radius_arcsec > 0:
            raise ConfigurationError(
                f"match_radius_arcsec must be positive, got {self.match_radius_arcsec}"
            )
        if self.brightness_rank_factor is not None and not self.brightness_rank_factor > 0:
            raise ConfigurationError("brightness_rank_factor must be positive or None")
        if self.edge_margin_px < 0:
            raise ConfigurationError("edge_margin_px must be >= 0")
        if not self.registration_radius_arcsec > 0:
            raise ConfigurationError("registration_radius_arcsec must be positive")
        if not self.registration_isolation_ratio >= 1:
            raise ConfigurationError("registration_isolation_ratio must be >= 1")
        if self.refine_sip_degree is not None and not 1 <= self.refine_sip_degree <= 5:
            raise ConfigurationError("refine_sip_degree must be None or 1-5")
        if self.refine_min_pairs < 6:
            raise ConfigurationError("refine_min_pairs must be >= 6")
        if not self.refine_clip_sigma > 0:
            raise ConfigurationError("refine_clip_sigma must be positive")
        if self.overlay_max_labels < 0:
            raise ConfigurationError("overlay_max_labels must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the configuration."""
        return asdict(self)


def _check_percentiles(name: str, lower: float, upper: float) -> None:
    if not (0.0 <= lower < upper <= 100.0):
        raise ConfigurationError(
            f"{name} percentiles must satisfy 0 <= lower < upper <= 100, "
            f"got lower={lower}, upper={upper}"
        )

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


def _check_percentiles(name: str, lower: float, upper: float) -> None:
    if not (0.0 <= lower < upper <= 100.0):
        raise ConfigurationError(
            f"{name} percentiles must satisfy 0 <= lower < upper <= 100, "
            f"got lower={lower}, upper={upper}"
        )

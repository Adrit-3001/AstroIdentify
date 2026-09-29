"""Centralized, explicit configuration for the preprocessing pipeline."""

from __future__ import annotations

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


def _check_percentiles(name: str, lower: float, upper: float) -> None:
    if not (0.0 <= lower < upper <= 100.0):
        raise ConfigurationError(
            f"{name} percentiles must satisfy 0 <= lower < upper <= 100, "
            f"got lower={lower}, upper={upper}"
        )

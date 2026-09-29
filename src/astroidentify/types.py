"""Core data models shared between pipeline stages.

Array conventions (used everywhere in AstroIdentify):

* Arrays are indexed ``[row, column]`` (``[y, x]``) for single-channel images and
  ``[row, column, channel]`` for colour images (channels are R, G, B).
* ``[0, 0]`` is the first pixel stored in the file. For JPEG/PNG that is the top-left
  pixel as displayed. For FITS it is FITS pixel (1, 1), which astronomy software displays
  at the *bottom*-left; FITS data is never flipped so that a WCS from the header stays
  valid. :attr:`AstronomyImage.display_origin` records which convention applies.
* Invalid pixels (FITS NaN/Inf/BLANK) are NaN in :attr:`AstronomyImage.data`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    from astropy.io.fits import Header

    from astroidentify.config import PreprocessingConfig

#: Floating-point dtype for all internal image arrays. float32 represents 8/16-bit raster
#: data exactly and has ample precision for typical FITS data while halving memory use.
WORKING_DTYPE = np.dtype(np.float32)

DisplayOrigin = Literal["upper", "lower"]


class ImageFormat(StrEnum):
    """Source file formats supported by the loader."""

    JPEG = "JPEG"
    PNG = "PNG"
    FITS = "FITS"


@dataclass(frozen=True, eq=False)
class AstronomyImage:
    """A loaded image in a format-independent representation.

    Attributes:
        data: Read-only ``WORKING_DTYPE`` array, shape ``(H, W)`` or ``(H, W, 3)``, in the
            source's own units (FITS values after BSCALE/BZERO; raster code values such as
            0-255). Invalid pixels are NaN.
        source_path: Absolute path of the source file.
        format: Detected source format (from file content, not only the extension).
        original_shape: Array shape as stored in the source, before squeezing singleton
            FITS axes or dropping an alpha channel.
        original_dtype: NumPy dtype name of the decoded source data.
        display_origin: ``"upper"`` if row 0 is displayed at the top (JPEG/PNG),
            ``"lower"`` if at the bottom (FITS convention).
        metadata: JSON-serializable, format-specific metadata (FITS header cards and
            summary, EXIF, PNG text chunks, decoding details).
        header: The selected FITS HDU header (kept for later WCS construction), or ``None``.
        warnings: Human-readable warnings raised while loading.
    """

    data: np.ndarray
    source_path: Path
    format: ImageFormat
    original_shape: tuple[int, ...]
    original_dtype: str
    display_origin: DisplayOrigin
    metadata: dict[str, Any] = field(default_factory=dict)
    header: Header | None = None
    warnings: tuple[str, ...] = ()

    @property
    def height(self) -> int:
        return int(self.data.shape[0])

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def channels(self) -> int:
        return 1 if self.data.ndim == 2 else int(self.data.shape[2])

    @property
    def is_color(self) -> bool:
        return self.data.ndim == 3


@dataclass(frozen=True)
class BackgroundEstimate:
    """A global background level and noise estimate for one image plane.

    Attributes:
        level: Robust background level (median of retained pixels), in data units.
        noise_sigma: Robust Gaussian-equivalent noise (scaled MAD), in data units.
        method: Short identifier of the estimator.
        noise_method: ``"mad"`` normally; ``"clipped_std"`` when the MAD was zero (heavily
            quantized or clipped backgrounds) and the standard deviation was used instead.
        n_pixels: Number of finite pixels considered.
        n_retained: Number of pixels left after sigma clipping.
        warnings: Warnings raised during estimation.
    """

    level: float
    noise_sigma: float
    method: str
    noise_method: str
    n_pixels: int
    n_retained: int
    warnings: tuple[str, ...] = ()

    @property
    def rejected_fraction(self) -> float:
        """Fraction of finite pixels rejected as outliers (a rough source-coverage hint)."""
        return 0.0 if self.n_pixels == 0 else 1.0 - self.n_retained / self.n_pixels


@dataclass(frozen=True)
class NormalizationParams:
    """An affine map ``(x - lower_value) / (upper_value - lower_value)``.

    Because the map is affine (and unclipped by default) it is exactly invertible, so
    later stages can convert thresholds and fluxes between data and normalized units.
    """

    method: str
    lower_percentile: float
    upper_percentile: float
    lower_value: float
    upper_value: float
    clip: bool
    degenerate: bool = False

    @property
    def scale(self) -> float:
        return self.upper_value - self.lower_value

    def apply(self, values: np.ndarray | float) -> np.ndarray | float:
        """Map data-unit values to normalized units (never modifies ``values``)."""
        result = (values - self.lower_value) / self.scale
        if self.clip:
            result = np.clip(result, 0.0, 1.0)
        return result

    def apply_to_sigma(self, sigma: float) -> float:
        """Convert a noise level (a difference of values) to normalized units."""
        return sigma / self.scale


@dataclass(frozen=True, eq=False)
class PreprocessingResult:
    """Everything later stages need from preprocessing, independent of source format.

    Attributes:
        image: The loaded image (original units, NaN for invalid pixels).
        normalized: Read-only ``WORKING_DTYPE`` array, same shape as ``image.data``, all
            finite. Invalid pixels are filled with the normalized background level.
        valid_mask: Read-only boolean ``(H, W)`` array, ``True`` where every channel was
            finite in the source.
        background: Background/noise estimate of the analysis plane (the image itself
            for grayscale, the per-pixel channel mean for colour), in data units.
        channel_backgrounds: Per-channel estimates for colour images, else empty.
        normalization: Parameters of the normalization applied to produce ``normalized``.
        diagnostics: JSON-serializable diagnostics (pixel statistics, warnings, ...).
        config: The configuration that produced this result.
    """

    image: AstronomyImage
    normalized: np.ndarray
    valid_mask: np.ndarray
    background: BackgroundEstimate
    channel_backgrounds: tuple[BackgroundEstimate, ...]
    normalization: NormalizationParams
    diagnostics: dict[str, Any]
    config: PreprocessingConfig

    @property
    def background_level(self) -> float:
        return self.background.level

    @property
    def noise_sigma(self) -> float:
        return self.background.noise_sigma

    @property
    def normalized_background_level(self) -> float:
        return float(self.normalization.apply(self.background.level))

    @property
    def normalized_noise_sigma(self) -> float:
        return self.normalization.apply_to_sigma(self.background.noise_sigma)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self.diagnostics.get("warnings", ()))

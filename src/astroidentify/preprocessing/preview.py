"""Human-oriented preview rendering (display only, never used for analysis).

The preview maps the configured display percentiles to black/white, optionally applies an
asinh stretch (the standard astronomical choice: roughly linear for faint signal and
logarithmic for bright stars), and converts to 8 bits. Colour channels share one scale so
colour balance is preserved. FITS previews are flipped vertically so they appear the way
astronomy viewers (e.g. DS9) show them; the analysis arrays are never flipped.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from astroidentify.config import PreprocessingConfig
from astroidentify.exceptions import OutputError
from astroidentify.preprocessing.normalize import robust_range
from astroidentify.types import AstronomyImage, ImageFormat

logger = logging.getLogger(__name__)


def resolve_stretch(
    image: AstronomyImage, config: PreprocessingConfig
) -> Literal["linear", "asinh"]:
    """Resolve ``"auto"``: FITS is usually linear sensor data and benefits from asinh;
    JPEG/PNG are usually already display-encoded, so a linear stretch is used."""
    if config.preview_stretch == "auto":
        return "asinh" if image.format is ImageFormat.FITS else "linear"
    return config.preview_stretch


def stretch_for_display(
    image: AstronomyImage, config: PreprocessingConfig | None = None
) -> np.ndarray:
    """Return the display-stretched ``uint8`` array in array orientation (never flipped)."""
    config = config or PreprocessingConfig()
    low, high, _ = robust_range(
        image.data, config.preview_lower_percentile, config.preview_upper_percentile
    )
    scaled = np.clip((image.data - low) / (high - low), 0.0, 1.0)
    scaled = np.nan_to_num(scaled, nan=0.0)
    if resolve_stretch(image, config) == "asinh":
        a = config.preview_asinh_softening
        scaled = np.arcsinh(scaled / a) / np.arcsinh(1.0 / a)
    return np.round(scaled * 255.0).astype(np.uint8)


def render_preview(image: AstronomyImage, config: PreprocessingConfig | None = None) -> Image.Image:
    """Render an 8-bit grayscale or RGB preview of ``image``."""
    config = config or PreprocessingConfig()
    pixels = stretch_for_display(image, config)
    if image.display_origin == "lower":
        pixels = np.flipud(pixels)
    preview = Image.fromarray(np.ascontiguousarray(pixels))  # uint8 (H, W) -> L, (H, W, 3) -> RGB

    max_dim = config.preview_max_dimension
    if max_dim is not None and max(preview.size) > max_dim:
        preview.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)  # keeps aspect ratio
    return preview


def save_preview(
    image: AstronomyImage, path: Path, config: PreprocessingConfig | None = None
) -> Path:
    """Render and save a PNG preview to ``path``."""
    try:
        # Low zlib effort: still lossless, several times faster on large noisy images.
        render_preview(image, config).save(path, format="PNG", compress_level=1)
    except OSError as exc:
        raise OutputError(f"could not write preview {path}: {exc}") from exc
    logger.info("Wrote preview %s", path)
    return path

"""The Milestone 1 preprocessing pipeline: load -> background/noise -> normalize.

Typical use::

    result = preprocess_image("data/raw/m57.fits")
    paths = save_outputs(result, "outputs/m57")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify.config import PreprocessingConfig
from astroidentify.preprocessing.background import (
    analysis_plane,
    estimate_background,
    estimate_difference_noise,
)
from astroidentify.preprocessing.loader import load_image
from astroidentify.preprocessing.normalize import compute_normalization, normalize
from astroidentify.types import AstronomyImage, BackgroundEstimate, PreprocessingResult

logger = logging.getLogger(__name__)

# Diagnostic threshold: a larger share of pixels at the minimum value suggests the
# background was clipped to black (common in processed JPEGs), which biases statistics.
CLIPPED_BACKGROUND_WARNING_FRACTION = 0.25
# Diagnostic threshold: global noise this many times the pixel-to-pixel noise means the
# "noise" is dominated by background structure rather than pixel noise.
STRUCTURED_BACKGROUND_NOISE_RATIO = 2.0

_CHANNEL_NAMES = ("R", "G", "B")


def preprocess_image(
    path: str | Path, config: PreprocessingConfig | None = None
) -> PreprocessingResult:
    """Load an image file and run the full preprocessing pipeline on it."""
    config = config or PreprocessingConfig()
    return preprocess(load_image(path, config), config)


def preprocess(
    image: AstronomyImage, config: PreprocessingConfig | None = None
) -> PreprocessingResult:
    """Estimate background/noise and normalize an already-loaded image.

    ``image`` is not modified; all returned arrays are new.
    """
    config = config or PreprocessingConfig()
    data = image.data
    valid_mask = np.all(np.isfinite(data), axis=2) if data.ndim == 3 else np.isfinite(data)
    valid_mask.flags.writeable = False

    plane = analysis_plane(data)
    background = _estimate(plane, config)
    channel_backgrounds = (
        tuple(_estimate(data[:, :, c], config) for c in range(data.shape[2]))
        if image.is_color
        else ()
    )
    difference_noise = estimate_difference_noise(plane)

    params = compute_normalization(
        data,
        lower_percentile=config.normalization_lower_percentile,
        upper_percentile=config.normalization_upper_percentile,
        clip=config.normalization_clip,
    )
    normalized = normalize(data, params, fill_value=float(params.apply(background.level)))

    diagnostics = _pixel_statistics(data, valid_mask)
    diagnostics["background_rejected_fraction"] = background.rejected_fraction
    diagnostics["difference_noise_sigma"] = difference_noise

    warnings = list(image.warnings)
    _collect(warnings, background.warnings, "")
    for name, estimate in zip(_CHANNEL_NAMES, channel_backgrounds, strict=False):
        _collect(warnings, estimate.warnings, f"channel {name}: ")
    if params.degenerate:
        _collect(
            warnings,
            (
                "normalization percentiles coincide (nearly constant image); "
                f"used range [{params.lower_value:.6g}, {params.upper_value:.6g}] instead",
            ),
        )
    if diagnostics["fraction_at_min"] > CLIPPED_BACKGROUND_WARNING_FRACTION:
        _collect(
            warnings,
            (
                f"{diagnostics['fraction_at_min']:.0%} of pixel values equal the minimum; the "
                "background may be clipped to black, so noise estimates may be unreliable",
            ),
        )
    if 0 < STRUCTURED_BACKGROUND_NOISE_RATIO * difference_noise < background.noise_sigma:
        _collect(
            warnings,
            (
                f"global noise sigma ({background.noise_sigma:.4g}) is much larger than the "
                f"pixel-to-pixel noise ({difference_noise:.4g}): the background has "
                "large-scale structure (nebulosity or gradients), which a single global "
                "background estimate cannot model",
            ),
        )
    diagnostics["warnings"] = warnings

    logger.info(
        "Background %.6g, noise sigma %.6g (%s)",
        background.level,
        background.noise_sigma,
        background.method,
    )
    return PreprocessingResult(
        image=image,
        normalized=normalized,
        valid_mask=valid_mask,
        background=background,
        channel_backgrounds=channel_backgrounds,
        normalization=params,
        diagnostics=diagnostics,
        config=config,
    )


def _collect(warnings: list[str], new: tuple[str, ...], prefix: str = "") -> None:
    for message in new:
        logger.warning("%s%s", prefix, message)
        warnings.append(prefix + message)


def _estimate(plane: np.ndarray, config: PreprocessingConfig) -> BackgroundEstimate:
    return estimate_background(
        plane,
        clip_sigma=config.background_clip_sigma,
        maxiters=config.background_clip_maxiters,
    )


def _pixel_statistics(data: np.ndarray, valid_mask: np.ndarray) -> dict[str, Any]:
    finite = data[np.isfinite(data)]
    data_min, data_max = float(finite.min()), float(finite.max())
    n_pixels = int(valid_mask.size)
    n_invalid = n_pixels - int(np.count_nonzero(valid_mask))
    return {
        "n_pixels": n_pixels,
        "n_invalid_pixels": n_invalid,
        "invalid_fraction": n_invalid / n_pixels,
        "min": data_min,
        "max": data_max,
        "mean": float(finite.mean(dtype=np.float64)),
        "std": float(finite.std(dtype=np.float64)),
        # Large fractions at the extremes indicate black clipping or saturation.
        "fraction_at_min": float(np.count_nonzero(finite == data_min)) / finite.size,
        "fraction_at_max": float(np.count_nonzero(finite == data_max)) / finite.size,
    }

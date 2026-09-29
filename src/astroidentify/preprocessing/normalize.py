"""Deterministic normalization for downstream numerical processing.

Method: a single affine map ``(x - p_lo) / (p_hi - p_lo)`` where ``p_lo``/``p_hi`` are
percentiles of all finite pixel values (all channels pooled, so colour ratios are kept).
With the defaults (1st / 99.5th percentiles) isolated bright stars cannot dominate the
scale, and because values outside [0, 1] are *not* clipped, faint signal below ``p_lo`` and
bright star cores above ``p_hi`` survive. This is not a display stretch; see ``preview``.
"""

from __future__ import annotations

import numpy as np

from astroidentify.exceptions import InvalidImageError
from astroidentify.types import WORKING_DTYPE, NormalizationParams

METHOD = "percentile"


def robust_range(
    data: np.ndarray, lower_percentile: float, upper_percentile: float
) -> tuple[float, float, bool]:
    """Return ``(low, high, degenerate)`` percentile bounds of the finite values of ``data``.

    If the percentiles coincide (e.g. mostly-constant images) the full finite range is used;
    if the image is constant, ``high = low + 1``. ``degenerate`` flags either fallback.

    Raises:
        InvalidImageError: If ``data`` has no finite values.
    """
    finite = np.asarray(data)[np.isfinite(data)]
    if finite.size == 0:
        raise InvalidImageError("cannot compute a value range: no finite pixel values")
    low, high = (float(v) for v in np.percentile(finite, [lower_percentile, upper_percentile]))
    if high > low:
        return low, high, False
    low, high = float(finite.min()), float(finite.max())
    if high > low:
        return low, high, True
    return low, low + 1.0, True


def compute_normalization(
    data: np.ndarray,
    *,
    lower_percentile: float,
    upper_percentile: float,
    clip: bool = False,
) -> NormalizationParams:
    """Derive normalization parameters from ``data`` (which is not modified)."""
    low, high, degenerate = robust_range(data, lower_percentile, upper_percentile)
    return NormalizationParams(
        method=METHOD,
        lower_percentile=lower_percentile,
        upper_percentile=upper_percentile,
        lower_value=low,
        upper_value=high,
        clip=clip,
        degenerate=degenerate,
    )


def normalize(data: np.ndarray, params: NormalizationParams, fill_value: float) -> np.ndarray:
    """Apply ``params`` to ``data``, returning a new read-only, all-finite array.

    Args:
        data: Image data in source units. Not modified.
        params: Normalization parameters.
        fill_value: Normalized value written where ``data`` is not finite. The pipeline uses
            the normalized background level so filled pixels do not look like sources.
    """
    normalized = np.asarray(params.apply(np.asarray(data, dtype=WORKING_DTYPE)), WORKING_DTYPE)
    if not np.all(np.isfinite(normalized)):
        normalized = np.where(np.isfinite(normalized), normalized, WORKING_DTYPE.type(fill_value))
    normalized.flags.writeable = False
    return normalized

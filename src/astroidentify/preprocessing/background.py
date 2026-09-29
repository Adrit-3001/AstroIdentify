"""Global background level and noise estimation.

Method: iterative sigma clipping (centre = median, width = MAD-based sigma) removes stars,
hot pixels and other outliers; the background is then the median of the retained pixels
and the noise is their scaled median absolute deviation (MAD). Plain median/MAD over all
pixels is biased upward in crowded or nebulous fields, which is why clipping is on by
default. Both are deterministic.

This is a single *global* estimate. Images with strong gradients (light pollution, vignetting)
will get an inflated noise value; a tiled background map can replace this module later
without changing its callers.
"""

from __future__ import annotations

import numpy as np
from astropy.stats import sigma_clip

from astroidentify.exceptions import InvalidImageError
from astroidentify.types import WORKING_DTYPE, BackgroundEstimate

#: 1 / Phi^-1(3/4). Scales the MAD so it equals the standard deviation for Gaussian noise.
MAD_TO_SIGMA = 1.482602218505602


def analysis_plane(data: np.ndarray) -> np.ndarray:
    """Return the 2-D plane used for scalar statistics.

    Grayscale data is returned as-is (not copied). Colour data is reduced to the unweighted
    mean of its channels: astronomical targets have arbitrary colours, so a perceptual
    luminance weighting has no special meaning here. A pixel is NaN if any channel is.
    """
    if data.ndim == 2:
        return data
    return data.mean(axis=2, dtype=WORKING_DTYPE)


def estimate_background(
    values: np.ndarray,
    *,
    clip_sigma: float | None = 3.0,
    maxiters: int = 5,
) -> BackgroundEstimate:
    """Estimate the background level and noise of an image plane.

    Args:
        values: Pixel values of any shape; non-finite values are ignored. Never modified.
        clip_sigma: Sigma-clipping threshold, or ``None`` to use all finite pixels.
        maxiters: Maximum number of clipping iterations.

    Returns:
        The estimate. Problems are reported in its ``warnings`` (not logged here) so the
        caller can attach context such as the channel name.

    Raises:
        InvalidImageError: If there are no finite values.
    """
    finite = np.asarray(values)[np.isfinite(values)]
    if finite.size == 0:
        raise InvalidImageError("cannot estimate background: no finite pixel values")

    notes: list[str] = []
    if clip_sigma is None:
        retained = finite
        method = "median"
    else:
        retained = _clip(finite, clip_sigma, maxiters, stdfunc="mad_std")
        method = f"sigma_clipped_median(sigma={clip_sigma:g}, maxiters={maxiters})"

    level = float(np.median(retained))
    noise = MAD_TO_SIGMA * float(np.median(np.abs(retained - level)))
    noise_method = "mad"

    if noise == 0.0:
        # More than half the pixels share one value (heavily quantized 8-bit data or a
        # background clipped to black), so the MAD carries no information. Fall back to a
        # standard deviation after std-based clipping, which still rejects stars.
        fallback = finite if clip_sigma is None else _clip(finite, clip_sigma, maxiters, "std")
        noise = float(np.std(fallback))
        noise_method = "clipped_std" if clip_sigma is not None else "std"
        if noise > 0.0:
            notes.append(
                "MAD noise estimate was zero (quantized or clipped background); "
                f"used standard deviation instead ({noise:.4g})"
            )
        else:
            notes.append(
                "noise estimate is zero: background pixels are identical after outlier "
                "rejection (noise below the data's quantization step, or a clipped background)"
            )

    return BackgroundEstimate(
        level=level,
        noise_sigma=noise,
        method=method,
        noise_method=noise_method,
        n_pixels=int(finite.size),
        n_retained=int(retained.size),
        warnings=tuple(notes),
    )


def estimate_difference_noise(plane: np.ndarray) -> float:
    """Estimate per-pixel noise from differences of horizontally adjacent pixels.

    ``sigma = MAD(x[:, i+1] - x[:, i]) * MAD_TO_SIGMA / sqrt(2)``. Smooth structure
    (nebulosity, gradients) nearly cancels in the difference, so unlike the global estimate
    this approximates pixel noise even in structured fields. It underestimates noise that is
    spatially correlated (JPEG compression, demosaicing, resampled or stacked images), so
    it is reported as a diagnostic rather than replacing the background noise estimate.

    Args:
        plane: 2-D array; non-finite differences are ignored. Never modified.
    """
    diff = np.diff(np.asarray(plane, dtype=np.float64), axis=1)
    diff = diff[np.isfinite(diff)]
    if diff.size == 0:
        return float("nan")
    mad = float(np.median(np.abs(diff - np.median(diff))))
    return MAD_TO_SIGMA * mad / float(np.sqrt(2.0))


def _clip(values: np.ndarray, sigma: float, maxiters: int, stdfunc: str) -> np.ndarray:
    retained = sigma_clip(
        values, sigma=sigma, maxiters=maxiters, cenfunc="median", stdfunc=stdfunc, masked=False
    )
    # Clipping cannot empty a non-empty sample (the median is always within bounds), but
    # guard anyway so a library change cannot silently yield NaN statistics.
    return retained if retained.size else values

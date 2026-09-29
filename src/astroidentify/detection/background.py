"""Local (spatially varying) background and RMS estimation.

Method: photutils ``Background2D``. The plane is divided into tiles of about
``background_box_size`` pixels (adjusted per axis so tiles cover the image evenly); in each
tile, 3-sigma-clipped pixels give a median background and a standard-deviation RMS. The
tile grid is median-filtered (suppressing tiles biased by bright stars or extended objects)
and interpolated back to full resolution. This follows gradients, vignetting and sky glow
that a single global value (Milestone 1) cannot.

The RMS map is floored so it can never be zero (a zero RMS would make every pixel a
detection): at the quantization noise of integer-coded data, ``step / sqrt(12 * channels)``
for a channel mean, and in any case at a tiny fraction of the median RMS.
"""

from __future__ import annotations

import logging
import math
import warnings

import numpy as np
from astropy.stats import SigmaClip
from photutils.background import Background2D, MedianBackground, StdBackgroundRMS

from astroidentify.config import DetectionConfig
from astroidentify.detection.types import DetectionPlane, LocalBackground
from astroidentify.exceptions import BackgroundEstimationError
from astroidentify.types import WORKING_DTYPE

logger = logging.getLogger(__name__)

# Clipping iterations inside each tile; converges well before this in practice.
_TILE_CLIP_MAXITERS = 10
# Absolute lower bound on the RMS relative to its median, for float data without a
# known quantization step.
_MIN_RMS_FRACTION_OF_MEDIAN = 1e-3


def estimate_local_background(
    plane: DetectionPlane,
    config: DetectionConfig | None = None,
    *,
    quantization_rms: float = 0.0,
) -> LocalBackground:
    """Model the background and RMS of ``plane``.

    Args:
        plane: The detection plane (never modified).
        config: Detection configuration (tile size, filter size, clipping).
        quantization_rms: RMS of the quantization noise of the plane (0 if unknown).

    Raises:
        BackgroundEstimationError: If the background cannot be modelled (e.g. every tile is
            masked) or the image has no measurable noise.
    """
    config = config or DetectionConfig()
    notes: list[str] = []
    height, width = plane.shape

    requested = config.background_box_size
    if requested > min(height, width):
        notes.append(
            f"background box size {requested} exceeds the image size; "
            f"using at most {min(requested, height)} x {min(requested, width)}"
        )
    box = (even_tile_size(height, requested), even_tile_size(width, requested))
    grid = (math.ceil(height / box[0]), math.ceil(width / box[1]))
    filter_size = config.background_filter_size
    if filter_size > min(grid):
        filter_size = 1  # too few tiles to median-filter meaningfully
        notes.append(f"only {grid[0]} x {grid[1]} background tiles; tile filtering disabled")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            model = Background2D(
                plane.data,
                box,
                mask=plane.invalid_mask if plane.has_invalid else None,
                filter_size=(filter_size, filter_size),
                sigma_clip=SigmaClip(
                    sigma=config.background_clip_sigma, maxiters=_TILE_CLIP_MAXITERS
                ),
                bkg_estimator=MedianBackground(),
                bkg_rms_estimator=StdBackgroundRMS(),
                exclude_percentile=config.background_exclude_percentile,
            )
            background = np.asarray(model.background, dtype=WORKING_DTYPE)
            rms = np.asarray(model.background_rms, dtype=WORKING_DTYPE)
    except ValueError as exc:
        raise BackgroundEstimationError(f"local background estimation failed: {exc}") from exc

    if not (np.all(np.isfinite(background)) and np.all(np.isfinite(rms))):
        raise BackgroundEstimationError("local background model contains non-finite values")

    positive = rms[rms > 0]
    median_rms = float(np.median(positive)) if positive.size else 0.0
    rms_floor = max(quantization_rms, _MIN_RMS_FRACTION_OF_MEDIAN * median_rms)
    if rms_floor <= 0:
        raise BackgroundEstimationError(
            "background RMS is zero everywhere: the image has no measurable noise"
        )
    floored_fraction = float(np.mean(rms < rms_floor))
    if floored_fraction > 0:
        notes.append(
            f"background RMS raised to the floor {rms_floor:.4g} in "
            f"{floored_fraction:.1%} of pixels"
        )
    rms = np.maximum(rms, WORKING_DTYPE.type(rms_floor))
    subtracted = plane.data - background

    for note in notes:
        logger.warning(note)
    for array in (background, rms, subtracted):
        array.flags.writeable = False
    return LocalBackground(
        background=background,
        rms=rms,
        subtracted=subtracted,
        box_size=box,
        filter_size=filter_size,
        rms_floor=rms_floor,
        warnings=tuple(notes),
    )


def even_tile_size(length: int, requested: int) -> int:
    """Tile size close to ``requested`` that splits ``length`` into near-equal tiles.

    Background2D interpolates tile values as if tile centres were on a regular grid; a small
    partial tile at the image edge breaks that assumption and shifts the whole map (a
    systematic error proportional to any gradient). Using ``ceil(length / n)`` with
    ``n = round(length / requested)`` leaves at most ``n - 1`` pixels of mismatch in total.
    """
    n_tiles = max(1, round(length / min(requested, length)))
    return math.ceil(length / n_tiles)


def quantization_rms(original_dtype: str, channels: int) -> float:
    """RMS quantization noise of a channel-mean plane of integer-coded data (else 0).

    Rounding to integers adds uniform noise with variance 1/12 per channel; averaging
    ``channels`` independent channels divides the variance by ``channels``.
    """
    if not np.issubdtype(np.dtype(original_dtype), np.integer):
        return 0.0
    return 1.0 / math.sqrt(12.0 * max(channels, 1))


def map_statistics(array: np.ndarray) -> dict[str, float]:
    """Min / median / max of a map, for metadata."""
    return {
        "min": float(np.min(array)),
        "median": float(np.median(array)),
        "max": float(np.max(array)),
    }

"""Build the 2-D detection plane from a Milestone 1 preprocessing result.

Rule: grayscale images are used as-is; colour images use the unweighted mean of their
channels (the same reduction Milestone 1 uses for its statistics, reused here rather than
duplicated). Stars have arbitrary colours, so no perceptual luminance weighting is applied,
and averaging three channels lowers per-pixel noise.

The plane stays in source units (not the normalized array) so fluxes and saturation relate
directly to the recorded pixel values. Invalid pixels are filled with the Milestone 1
global background and masked, so every later step sees finite data and ignores them.
"""

from __future__ import annotations

import numpy as np

from astroidentify.detection.types import DetectionPlane
from astroidentify.exceptions import InvalidDetectionInputError
from astroidentify.preprocessing.background import analysis_plane
from astroidentify.types import WORKING_DTYPE, PreprocessingResult


def make_detection_plane(preprocessing: PreprocessingResult) -> DetectionPlane:
    """Derive the detection plane; the preprocessing arrays are never modified."""
    data = preprocessing.image.data
    if not (data.ndim == 2 or (data.ndim == 3 and data.shape[2] >= 1)):
        raise InvalidDetectionInputError(f"unsupported image array shape {data.shape}")

    invalid = ~np.asarray(preprocessing.valid_mask, dtype=bool)
    if invalid.shape != data.shape[:2]:
        raise InvalidDetectionInputError(
            f"valid mask shape {invalid.shape} does not match image shape {data.shape[:2]}"
        )

    fill_value = float(preprocessing.background_level)
    plane = np.array(analysis_plane(data), dtype=WORKING_DTYPE, copy=True)
    plane[invalid] = fill_value
    if not np.all(np.isfinite(plane)):
        raise InvalidDetectionInputError("detection plane contains non-finite values")

    plane.flags.writeable = False
    invalid.flags.writeable = False
    return DetectionPlane(
        data=plane,
        invalid_mask=invalid,
        method="channel_mean" if data.ndim == 3 else "identity",
        fill_value=fill_value,
    )

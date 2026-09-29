from __future__ import annotations

import numpy as np
import pytest

from astroidentify.detection.plane import make_detection_plane
from astroidentify.types import ImageFormat
from tests.detection.synthetic import as_preprocessed, render_field


def test_grayscale_plane_is_identity() -> None:
    data = render_field((40, 50))
    result = as_preprocessed(data)
    plane = make_detection_plane(result)

    assert plane.method == "identity"
    assert plane.shape == (40, 50)
    assert plane.data.dtype == np.float32
    np.testing.assert_array_equal(plane.data, result.image.data)
    assert not plane.has_invalid


def test_rgb_plane_is_channel_mean_and_sources_untouched() -> None:
    base = render_field((40, 50))
    rgb = np.stack([base, 2 * base, 3 * base], axis=-1)
    result = as_preprocessed(rgb, image_format=ImageFormat.PNG)
    data_before = result.image.data.copy()
    normalized_before = result.normalized.copy()

    plane = make_detection_plane(result)

    assert plane.method == "channel_mean"
    assert plane.data.ndim == 2 and plane.shape == (40, 50)
    assert np.all(np.isfinite(plane.data))
    np.testing.assert_allclose(plane.data, 2 * base.astype(np.float32), rtol=1e-5)
    np.testing.assert_array_equal(result.image.data, data_before)
    np.testing.assert_array_equal(result.normalized, normalized_before)
    assert not np.shares_memory(plane.data, result.image.data)


def test_plane_is_read_only() -> None:
    plane = make_detection_plane(as_preprocessed(render_field((20, 20))))
    with pytest.raises(ValueError):
        plane.data[0, 0] = 0.0


def test_invalid_pixels_are_filled_and_masked() -> None:
    data = render_field((30, 30))
    data[5:8, 5:8] = np.nan
    result = as_preprocessed(data)
    plane = make_detection_plane(result)

    assert np.all(np.isfinite(plane.data))
    assert plane.invalid_mask[5:8, 5:8].all() and plane.invalid_mask.sum() == 9
    assert np.all(plane.data[5:8, 5:8] == np.float32(result.background_level))

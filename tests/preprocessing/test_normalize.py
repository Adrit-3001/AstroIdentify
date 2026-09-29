from __future__ import annotations

import numpy as np
import pytest

from astroidentify.config import PreprocessingConfig
from astroidentify.exceptions import ConfigurationError
from astroidentify.preprocessing.normalize import compute_normalization, normalize, robust_range
from astroidentify.types import WORKING_DTYPE
from tests.conftest import star_field


def _params(data: np.ndarray, clip: bool = False):
    return compute_normalization(data, lower_percentile=1.0, upper_percentile=99.5, clip=clip)


def test_percentiles_map_to_zero_and_one() -> None:
    data = np.arange(10001, dtype=np.float32)  # percentiles are exact on this ramp
    params = _params(data)
    assert params.lower_value == pytest.approx(100.0)
    assert params.upper_value == pytest.approx(9950.0)

    normalized = normalize(data, params, fill_value=0.0)
    assert normalized.dtype == WORKING_DTYPE
    assert normalized[100] == pytest.approx(0.0)
    assert normalized[9950] == pytest.approx(1.0)


def test_unclipped_by_default_preserves_extremes() -> None:
    data = star_field()
    params = _params(data)
    normalized = normalize(data, params, fill_value=0.0)
    assert normalized.min() < 0.0  # faint values below the 1st percentile survive
    assert normalized.max() > 1.0  # star cores above the 99.5th percentile survive
    # Affine and unclipped, so exactly invertible.
    restored = normalized.astype(np.float64) * params.scale + params.lower_value
    np.testing.assert_allclose(restored, data, rtol=1e-5, atol=1e-3)


def test_clip_option() -> None:
    data = star_field()
    normalized = normalize(data, _params(data, clip=True), fill_value=0.0)
    assert normalized.min() == 0.0 and normalized.max() == 1.0


def test_bright_stars_do_not_set_the_scale() -> None:
    faint = star_field(shape=(256, 256), stars=())
    bright = star_field(shape=(256, 256), stars=[(60, 60, 1e5), (180, 200, 1e5)])
    # The stars peak ~1000x above the background, but cover < 0.5% of the pixels, so they
    # move the 99.5th percentile only slightly (a max-based scale would be ~1000x larger).
    assert bright.max() > 1e5
    assert _params(bright).upper_value == pytest.approx(_params(faint).upper_value, rel=0.03)


def test_nan_filled_and_output_finite() -> None:
    data = star_field().astype(np.float32)
    data[5:10, 5:10] = np.nan
    params = _params(data)
    normalized = normalize(data, params, fill_value=0.25)
    assert np.all(np.isfinite(normalized))
    assert np.all(normalized[5:10, 5:10] == np.float32(0.25))
    assert params.lower_value == pytest.approx(np.nanpercentile(data, 1.0))


def test_input_not_modified_and_output_read_only() -> None:
    data = star_field()
    original = data.copy()
    normalized = normalize(data, _params(data), fill_value=0.0)
    np.testing.assert_array_equal(data, original)
    assert normalized is not data
    with pytest.raises(ValueError):
        normalized[0, 0] = 0.0


def test_color_channels_share_one_scale() -> None:
    gray = star_field()
    rgb = np.stack([gray, 2 * gray, 3 * gray], axis=-1)
    normalized = normalize(rgb, _params(rgb), fill_value=0.0)
    params = _params(rgb)
    # Same affine map on every channel: differences between channels scale uniformly.
    np.testing.assert_allclose(
        normalized[..., 1] - normalized[..., 0], gray / params.scale, rtol=1e-4, atol=1e-5
    )


def test_constant_image_is_degenerate_but_finite() -> None:
    data = np.full((16, 16), 42.0)
    params = _params(data)
    assert params.degenerate
    normalized = normalize(data, params, fill_value=0.0)
    assert np.all(normalized == 0.0)


def test_mostly_constant_image_uses_full_range() -> None:
    data = np.zeros((100, 100))
    data[0, :5] = 50.0  # 0.05% of pixels, above the 99.5th percentile
    low, high, degenerate = robust_range(data, 1.0, 99.5)
    assert (low, high, degenerate) == (0.0, 50.0, True)


@pytest.mark.parametrize(("lower", "upper"), [(99.5, 1.0), (5.0, 5.0), (-1.0, 50.0), (1.0, 100.5)])
def test_invalid_percentiles_rejected(lower: float, upper: float) -> None:
    with pytest.raises(ConfigurationError):
        PreprocessingConfig(
            normalization_lower_percentile=lower, normalization_upper_percentile=upper
        )

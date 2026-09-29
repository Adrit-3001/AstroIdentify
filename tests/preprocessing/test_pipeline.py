from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astroidentify import PreprocessingConfig, preprocess, preprocess_image
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.types import WORKING_DTYPE, ImageFormat
from tests.conftest import BACKGROUND, NOISE, star_field


def test_fits_end_to_end_estimates(simple_fits: Path) -> None:
    result = preprocess_image(simple_fits)

    assert result.normalized.shape == result.image.data.shape == (128, 160)
    assert result.normalized.dtype == WORKING_DTYPE
    assert np.all(np.isfinite(result.normalized))
    assert result.background_level == pytest.approx(BACKGROUND, abs=0.5)
    assert result.noise_sigma == pytest.approx(NOISE, rel=0.05)
    assert result.channel_backgrounds == ()
    assert result.valid_mask.shape == (128, 160) and result.valid_mask.all()


@pytest.mark.parametrize("fixture", ["gray_png", "rgb_png", "rgb_jpeg"])
def test_raster_end_to_end(fixture: str, request: pytest.FixtureRequest) -> None:
    path: Path = request.getfixturevalue(fixture)
    result = preprocess_image(path)

    assert result.normalized.shape == result.image.data.shape
    assert result.valid_mask.shape == result.image.data.shape[:2]
    assert np.all(np.isfinite(result.normalized))
    assert result.noise_sigma > 0
    if result.image.is_color:
        assert len(result.channel_backgrounds) == 3


def test_rgb_channel_estimates(rgb_png: Path) -> None:
    result = preprocess_image(rgb_png)
    levels = [b.level for b in result.channel_backgrounds]
    # Channels were written as 0.4x, 0.5x and 0.6x the same star field (background 100).
    assert levels == pytest.approx([40.0, 50.0, 60.0], abs=1.0)
    assert result.background_level == pytest.approx(50.0, abs=1.0)


def test_normalized_background_is_consistent(simple_fits: Path) -> None:
    result = preprocess_image(simple_fits)
    norm = result.normalization
    assert result.normalized_background_level == pytest.approx(
        (result.background_level - norm.lower_value) / norm.scale
    )
    assert result.normalized_noise_sigma == pytest.approx(result.noise_sigma / norm.scale)
    assert float(np.median(result.normalized)) == pytest.approx(
        result.normalized_background_level, abs=0.02
    )


def test_source_file_not_modified(simple_fits: Path, rgb_jpeg: Path) -> None:
    for path in (simple_fits, rgb_jpeg):
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        preprocess_image(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_loaded_image_not_mutated() -> None:
    data = star_field()
    data[3, 3] = np.nan
    image = image_from_array(data, image_format=ImageFormat.FITS)
    snapshot = image.data.copy()

    result = preprocess(image)
    np.testing.assert_array_equal(image.data, snapshot)  # NaN positions compare equal
    assert result.image is image
    assert not np.shares_memory(result.normalized, image.data)


def test_nan_inf_handling_is_deterministic(fits_writer: Callable[..., Path]) -> None:
    data = star_field().astype(np.float32)
    data[10:14, 20:24] = np.nan
    data[50, 50] = np.inf
    data[51, 51] = -np.inf
    path = fits_writer("holes.fits", fits.PrimaryHDU(data))

    first = preprocess_image(path)
    second = preprocess_image(path)
    np.testing.assert_array_equal(first.normalized, second.normalized)
    assert first.background == second.background
    assert first.normalization == second.normalization

    assert first.diagnostics["n_invalid_pixels"] == 16 + 2
    assert not first.valid_mask[10, 20] and not first.valid_mask[50, 50]
    assert np.all(np.isfinite(first.normalized))
    fill = np.float32(first.normalized_background_level)
    assert first.normalized[10, 20] == fill
    assert first.normalized[50, 50] == fill == first.normalized[51, 51]


def test_config_is_respected(simple_fits: Path) -> None:
    config = PreprocessingConfig(
        normalization_lower_percentile=5.0,
        normalization_upper_percentile=95.0,
        background_clip_sigma=None,
    )
    result = preprocess_image(simple_fits, config)
    assert result.config is config
    assert result.normalization.lower_percentile == 5.0
    assert result.background.method == "median"


def test_clipped_background_warning() -> None:
    data = np.zeros((64, 64))
    data[40:, :] = np.random.default_rng(0).normal(20.0, 3.0, (24, 64))
    result = preprocess(image_from_array(data, image_format=ImageFormat.PNG))
    assert result.diagnostics["fraction_at_min"] > 0.5
    assert any("clipped to black" in w for w in result.warnings)


def test_structured_background_warning() -> None:
    rows = np.mgrid[:128, :128][0]
    data = 1000.0 + 800.0 * np.sin(rows / 20.0)
    data += np.random.default_rng(5).normal(0.0, 5.0, data.shape)
    result = preprocess(image_from_array(data, image_format=ImageFormat.FITS))

    assert result.diagnostics["difference_noise_sigma"] == pytest.approx(5.0, rel=0.1)
    assert any("large-scale structure" in w for w in result.warnings)


def test_no_structure_warning_for_flat_background(simple_fits: Path) -> None:
    result = preprocess_image(simple_fits)
    assert result.diagnostics["difference_noise_sigma"] == pytest.approx(NOISE, rel=0.1)
    assert result.warnings == ()

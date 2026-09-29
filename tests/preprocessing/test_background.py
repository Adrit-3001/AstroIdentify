from __future__ import annotations

import numpy as np
import pytest

from astroidentify.exceptions import InvalidImageError
from astroidentify.preprocessing.background import (
    MAD_TO_SIGMA,
    analysis_plane,
    estimate_background,
    estimate_difference_noise,
)
from tests.conftest import star_field


def test_mad_constant() -> None:
    # MAD of a standard normal is Phi^-1(3/4) = 0.6744897...
    assert pytest.approx(1 / 0.6744897501960817) == MAD_TO_SIGMA


def test_pure_gaussian_background() -> None:
    values = np.random.default_rng(1).normal(250.0, 8.0, (400, 400))
    estimate = estimate_background(values)
    assert estimate.level == pytest.approx(250.0, abs=0.1)
    assert estimate.noise_sigma == pytest.approx(8.0, rel=0.02)
    assert estimate.noise_method == "mad"
    assert estimate.n_pixels == values.size


def test_background_is_robust_to_stars() -> None:
    # 25 bright stars whose faint wings touch ~9% of the pixels.
    stars = [(r, c, 20000.0) for r in range(20, 200, 40) for c in range(20, 200, 40)]
    values = star_field(shape=(200, 200), background=100.0, noise=5.0, stars=stars, fwhm=4.0)

    estimate = estimate_background(values)
    assert estimate.level == pytest.approx(100.0, abs=0.3)
    assert estimate.noise_sigma == pytest.approx(5.0, rel=0.03)
    assert estimate.rejected_fraction > 0.01  # the stars were clipped

    # Plain median/MAD is noticeably biased by the star wings; clipping removes most of it.
    unclipped = estimate_background(values, clip_sigma=None)
    assert unclipped.method == "median"
    assert unclipped.level - 100.0 > 2 * (estimate.level - 100.0)
    assert unclipped.noise_sigma > 1.1 * estimate.noise_sigma


def test_non_finite_values_are_ignored() -> None:
    values = np.random.default_rng(2).normal(10.0, 1.0, (100, 100)).astype(np.float32)
    values[:10] = np.nan
    values[10:12] = np.inf
    estimate = estimate_background(values)
    assert estimate.n_pixels == 100 * 88
    assert np.isfinite(estimate.level) and np.isfinite(estimate.noise_sigma)
    assert estimate.level == pytest.approx(10.0, abs=0.05)


def test_input_not_modified() -> None:
    values = star_field()
    original = values.copy()
    estimate_background(values)
    np.testing.assert_array_equal(values, original)


def test_deterministic() -> None:
    values = star_field()
    assert estimate_background(values) == estimate_background(values)


def test_constant_image_has_zero_noise_with_warning() -> None:
    estimate = estimate_background(np.full((32, 32), 7.0))
    assert estimate.level == 7.0
    assert estimate.noise_sigma == 0.0
    assert estimate.warnings


def test_zero_mad_falls_back_to_std() -> None:
    # 60% of pixels at exactly zero (a background clipped to black), the rest noisy.
    rng = np.random.default_rng(3)
    values = np.zeros(10000)
    values[6000:] = np.abs(rng.normal(0.0, 3.0, 4000)).round()
    estimate = estimate_background(values)
    assert estimate.level == 0.0
    assert estimate.noise_method == "clipped_std"
    assert estimate.noise_sigma > 0.0
    assert estimate.warnings


def test_no_finite_values() -> None:
    with pytest.raises(InvalidImageError):
        estimate_background(np.full((4, 4), np.nan))


def test_analysis_plane() -> None:
    gray = np.ones((4, 5), dtype=np.float32)
    assert analysis_plane(gray) is gray

    rgb = np.stack([np.full((4, 5), v, np.float32) for v in (1.0, 2.0, 6.0)], axis=-1)
    rgb[0, 0, 1] = np.nan
    plane = analysis_plane(rgb)
    assert plane.shape == (4, 5)
    assert plane[1, 1] == pytest.approx(3.0)
    assert np.isnan(plane[0, 0])


def test_difference_noise_matches_gaussian_noise() -> None:
    assert estimate_difference_noise(star_field(shape=(300, 300))) == pytest.approx(5.0, rel=0.03)


def test_difference_noise_ignores_large_scale_structure() -> None:
    rows, cols = np.mgrid[:200, :200]
    gradient = 5000.0 * np.sin(rows / 40.0) + 20.0 * cols  # "nebulosity" + gradient
    values = gradient + np.random.default_rng(4).normal(0.0, 5.0, (200, 200))
    values[50, :] = np.nan

    assert estimate_background(values).noise_sigma > 100.0  # global spread, not noise
    assert estimate_difference_noise(values) == pytest.approx(5.0, rel=0.05)

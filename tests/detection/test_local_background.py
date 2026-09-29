from __future__ import annotations

import numpy as np
import pytest

from astroidentify.config import DetectionConfig
from astroidentify.detection.background import (
    estimate_local_background,
    even_tile_size,
    quantization_rms,
)
from astroidentify.detection.plane import make_detection_plane
from astroidentify.exceptions import BackgroundEstimationError
from tests.detection.synthetic import Star, as_preprocessed, grid_stars, render_field


def _model(data: np.ndarray, config: DetectionConfig | None = None, q: float = 0.0):
    plane = make_detection_plane(as_preprocessed(data))
    return plane, estimate_local_background(plane, config, quantization_rms=q)


def test_flat_background_and_noise() -> None:
    _, model = _model(render_field((256, 256), background=100.0, noise=5.0))
    assert np.all(np.isfinite(model.background)) and np.all(np.isfinite(model.rms))
    assert np.abs(model.background - 100.0).max() < 1.0
    assert np.median(model.rms) == pytest.approx(5.0, rel=0.05)
    assert model.box_size == (64, 64)


def test_tiles_cover_image_evenly() -> None:
    assert even_tile_size(1920, 64) == 64 and even_tile_size(2560, 64) == 64
    assert even_tile_size(200, 64) == 67  # 3 tiles of 67 rather than 3 x 64 + 8
    assert even_tile_size(30, 64) == 30
    _, model = _model(render_field((200, 240), gradient=(0.1, 0.05), noise=3.0))
    assert abs(float(np.median(model.subtracted))) < 0.5  # no systematic offset


def test_gradient_is_followed() -> None:
    gradient = (0.2, -0.1)  # per pixel in x and y
    shape = (256, 320)
    _, model = _model(render_field(shape, background=500.0, gradient=gradient, noise=3.0))
    rows, cols = np.mgrid[: shape[0], : shape[1]]
    truth = 500.0 + gradient[0] * cols + gradient[1] * rows
    error = np.abs(model.background - truth)
    global_error = np.abs(np.median(truth) - truth)
    # Away from the outermost tile ring, errors stay well below the 12.8-count change across
    # one tile. The 3x3 tile median filter compresses gradients in the edge tiles (a known
    # property of the tile-grid method, documented in the README).
    interior = (slice(64, -64), slice(64, -64))
    assert error[interior].max() < 5.0
    assert np.median(error) < 0.25 * np.median(global_error)
    # The gradient inside each tile adds its own spread to the tile RMS (documented).
    assert 3.0 <= np.median(model.rms) < 6.0


def test_background_robust_to_bright_stars() -> None:
    shape = (256, 256)
    stars = grid_stars(shape, spacing=32, amplitude=3000.0, fwhm=4.0, margin=16)
    _, model = _model(render_field(shape, background=100.0, noise=5.0, stars=stars))
    assert np.abs(np.median(model.background) - 100.0) < 1.0
    assert np.median(model.rms) == pytest.approx(5.0, rel=0.15)


def test_subtracted_plane_and_no_mutation() -> None:
    plane, model = _model(render_field((128, 128)))
    before = plane.data.copy()
    np.testing.assert_allclose(model.subtracted, plane.data - model.background, atol=1e-4)
    np.testing.assert_array_equal(plane.data, before)
    with pytest.raises(ValueError):
        model.background[0, 0] = 0.0


def test_small_image_clamps_box() -> None:
    _, model = _model(render_field((30, 40), noise=2.0), DetectionConfig(background_box_size=64))
    assert model.box_size == (30, 40)
    assert model.filter_size == 1
    assert any("exceeds the image size" in w for w in model.warnings)
    assert np.all(np.isfinite(model.background))


def test_rms_floor_for_quantized_flat_data() -> None:
    q = quantization_rms("uint8", 3)
    assert q == pytest.approx(1 / np.sqrt(36))
    _, model = _model(np.full((64, 64), 12.0), q=q)
    assert model.rms_floor == pytest.approx(q)
    assert np.all(model.rms >= np.float32(q) * 0.999)


def test_no_measurable_noise_is_an_error() -> None:
    with pytest.raises(BackgroundEstimationError, match="no measurable noise"):
        _model(np.full((64, 64), 12.0))


def test_quantization_rms_zero_for_float_data() -> None:
    assert quantization_rms("float32", 1) == 0.0


def test_star_positions_do_not_leave_holes() -> None:
    star = Star(64.0, 64.0, 5000.0, fwhm=5.0)
    _, model = _model(render_field((128, 128), stars=[star]))
    assert abs(float(model.background[64, 64]) - 100.0) < 3.0

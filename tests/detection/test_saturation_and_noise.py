from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits
from scipy import ndimage

from astroidentify.config import DetectionConfig
from astroidentify.detection.background import estimate_local_background
from astroidentify.detection.measurements import empirical_noise_factor
from astroidentify.detection.plane import make_detection_plane
from astroidentify.detection.saturation import (
    CENTROID_SATURATED_CORE,
    consolidate_saturated_cores,
    resolve_saturation_level,
    saturated_pixel_mask,
)
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.types import ImageFormat
from tests.detection.synthetic import as_preprocessed, render_field


def _disk(shape: tuple[int, int], x: float, y: float, radius: float) -> np.ndarray:
    rows, cols = np.mgrid[: shape[0], : shape[1]]
    return (cols - x) ** 2 + (rows - y) ** 2 <= radius**2


def test_candidates_on_one_core_are_consolidated() -> None:
    saturated = _disk((100, 100), 40.0, 50.0, 8.0)
    xy = np.array([[46.0, 50.0], [34.0, 51.0], [80.0, 80.0]])  # two on the core, one far
    cores = consolidate_saturated_cores(
        xy, np.array([10.0, 20.0, 5.0]), ["daofind"] * 3, saturated, fwhm=4.0
    )

    primary = 1  # the brighter of the two on the core
    assert cores.centroid_method[primary] == CENTROID_SATURATED_CORE
    assert cores.xy[primary] == pytest.approx([40.0, 50.0], abs=0.05)
    assert list(cores.primary_index) == [primary, -1, -1]
    assert cores.xy[2] == pytest.approx([80.0, 80.0])
    assert cores.core_axis_ratio[0] == pytest.approx(1.0, abs=0.05)
    assert cores.core_area[0] == saturated.sum() and cores.core_area[2] == 0
    assert np.isnan(cores.core_axis_ratio[2])


def test_elongated_region_has_low_axis_ratio() -> None:
    saturated = np.zeros((60, 100), dtype=bool)
    saturated[28:32, 20:80] = True  # 60 x 4 bar
    cores = consolidate_saturated_cores(
        np.array([[50.0, 30.0]]), np.array([1.0]), ["daofind"], saturated, fwhm=4.0
    )
    assert cores.core_axis_ratio[0] < 0.1


def test_no_saturation_leaves_candidates_untouched() -> None:
    xy = np.array([[10.0, 10.0]])
    cores = consolidate_saturated_cores(
        xy, np.array([1.0]), ["peak_com"], np.zeros((20, 20), bool), 3.0
    )
    assert cores.centroid_method == ["peak_com"]
    np.testing.assert_array_equal(cores.xy, xy)


def test_saturation_level_resolution() -> None:
    raster = image_from_array(
        np.zeros((16, 16)),
        image_format=ImageFormat.PNG,
        metadata={"raster": {"nominal_max": 65535}},
    )
    assert resolve_saturation_level(raster, DetectionConfig()) == (65535.0, "raster_nominal_max")
    assert resolve_saturation_level(raster, DetectionConfig(saturation_level=250.0)) == (
        250.0,
        "config",
    )
    header = fits.Header()
    header["SATURATE"] = 60000.0
    fits_image = image_from_array(np.zeros((16, 16)), image_format=ImageFormat.FITS, header=header)
    assert resolve_saturation_level(fits_image, DetectionConfig()) == (
        60000.0,
        "fits_saturate_keyword",
    )
    bare = image_from_array(np.zeros((16, 16)), image_format=ImageFormat.FITS)
    assert resolve_saturation_level(bare, DetectionConfig()) == (None, "unknown")


def test_saturated_pixel_mask_uses_any_channel() -> None:
    rgb = np.zeros((8, 8, 3))
    rgb[2, 3, 2] = 255
    image = image_from_array(rgb, image_format=ImageFormat.PNG, config=None)
    mask = saturated_pixel_mask(image, 255.0)
    assert mask.shape == (8, 8) and mask[2, 3] and mask.sum() == 1
    assert not saturated_pixel_mask(image, None).any()


def _background(data: np.ndarray):
    plane = make_detection_plane(as_preprocessed(data))
    return estimate_local_background(plane, DetectionConfig())


def test_noise_factor_is_one_for_white_noise() -> None:
    model = _background(render_field((256, 256), noise=5.0, seed=4))
    factor, count = empirical_noise_factor(
        model, np.empty((0, 2)), 4.0, np.zeros((256, 256), bool), 30
    )
    assert count > 100
    assert factor == pytest.approx(1.0, abs=0.15)


def test_noise_factor_detects_correlated_noise() -> None:
    white = np.random.default_rng(5).normal(0.0, 1.0, (256, 256))
    correlated = 100.0 + 5.0 * ndimage.gaussian_filter(white, 2.0) / 0.14  # rescale to ~5
    model = _background(correlated)
    factor, _ = empirical_noise_factor(model, np.empty((0, 2)), 4.0, np.zeros((256, 256), bool), 30)
    assert factor > 3.0


def test_noise_factor_needs_enough_apertures() -> None:
    model = _background(render_field((40, 40), noise=5.0))
    factor, count = empirical_noise_factor(
        model, np.empty((0, 2)), 4.0, np.zeros((40, 40), bool), 30
    )
    assert factor == 1.0 and count < 30

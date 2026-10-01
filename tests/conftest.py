"""Shared synthetic test data.

Images are generated on the fly with fixed seeds so numerical expectations are exact and
no binary fixtures need to be committed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

# Plate-solving fixtures (fake solve-field, index dir) shared by astrometry and CLI tests.
pytest_plugins = ["tests.astrometry.fixtures", "tests.catalogs.conftest_fixtures"]

BACKGROUND = 100.0
NOISE = 5.0
STARS: tuple[tuple[int, int, float], ...] = ((20, 30, 4000.0), (60, 90, 2500.0), (100, 15, 6000.0))


def star_field(
    shape: tuple[int, int] = (128, 160),
    background: float = BACKGROUND,
    noise: float = NOISE,
    stars: Sequence[tuple[int, int, float]] = STARS,
    fwhm: float = 3.0,
    seed: int = 0,
) -> np.ndarray:
    """Gaussian noise on a flat background plus Gaussian stars ``(row, col, peak)``."""
    rng = np.random.default_rng(seed)
    image = rng.normal(background, noise, shape)
    rows, cols = np.mgrid[: shape[0], : shape[1]]
    sigma = fwhm / 2.3548
    for row, col, peak in stars:
        image += peak * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2 * sigma**2))
    return image


def to_uint8(image: np.ndarray, scale: float = 0.5) -> np.ndarray:
    """Scale a star field into 8-bit range (background ~50 DN, noise ~2.5 DN)."""
    return np.clip(np.round(image * scale), 0, 255).astype(np.uint8)


@pytest.fixture
def star_data() -> np.ndarray:
    return star_field()


@pytest.fixture
def gray_png(tmp_path: Path) -> Path:
    path = tmp_path / "gray.png"
    Image.fromarray(to_uint8(star_field())).save(path)
    return path


@pytest.fixture
def rgb_png(tmp_path: Path) -> Path:
    path = tmp_path / "rgb.png"
    gray = star_field()
    rgb = np.stack([to_uint8(gray, 0.4), to_uint8(gray, 0.5), to_uint8(gray, 0.6)], axis=-1)
    Image.fromarray(rgb).save(path)
    return path


@pytest.fixture
def rgb_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "rgb.jpg"
    gray = to_uint8(star_field())
    Image.fromarray(np.stack([gray, gray, gray], axis=-1)).save(path, quality=95)
    return path


@pytest.fixture
def fits_writer(tmp_path: Path) -> Callable[..., Path]:
    """Write HDUs to a FITS file: ``fits_writer("name.fits", hdu, ...)``."""

    def write(name: str, *hdus: fits.hdu.base.ExtensionHDU | fits.PrimaryHDU) -> Path:
        path = tmp_path / name
        fits.HDUList(list(hdus)).writeto(path)
        return path

    return write


@pytest.fixture
def simple_fits(fits_writer: Callable[..., Path]) -> Path:
    header = fits.Header()
    header["OBJECT"] = "SYNTHETIC"
    header["EXPTIME"] = (30.0, "exposure time [s]")
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["HISTORY"] = "first history card"
    header["HISTORY"] = "second history card"
    return fits_writer(
        "field.fits", fits.PrimaryHDU(star_field().astype(np.float32), header=header)
    )

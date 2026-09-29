"""Synthetic star fields with known truth for detection tests (deterministic seeds)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from astroidentify import PreprocessingResult, preprocess
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.types import ImageFormat

FWHM_TO_SIGMA = 1.0 / (2.0 * np.sqrt(2.0 * np.log(2.0)))


@dataclass(frozen=True)
class Star:
    """A 2-D Gaussian star at array position (x = column, y = row)."""

    x: float
    y: float
    amplitude: float
    fwhm: float = 4.0
    axis_ratio: float = 1.0  # minor / major; the major axis lies along x


def render_field(
    shape: tuple[int, int] = (200, 240),
    *,
    background: float = 100.0,
    gradient: tuple[float, float] = (0.0, 0.0),
    noise: float = 5.0,
    stars: Sequence[Star] = (),
    seed: int = 0,
) -> np.ndarray:
    """Background (+ linear gradient per pixel in x, y) + Gaussian noise + stars."""
    rows, cols = np.mgrid[: shape[0], : shape[1]].astype(float)
    image = background + gradient[0] * cols + gradient[1] * rows
    if noise > 0:
        image = image + np.random.default_rng(seed).normal(0.0, noise, shape)
    for star in stars:
        sigma_x = star.fwhm * FWHM_TO_SIGMA
        sigma_y = sigma_x * star.axis_ratio
        image = image + star.amplitude * np.exp(
            -((cols - star.x) ** 2) / (2 * sigma_x**2) - ((rows - star.y) ** 2) / (2 * sigma_y**2)
        )
    return image


def grid_stars(
    shape: tuple[int, int], spacing: int, amplitude: float, fwhm: float, margin: int = 20
) -> list[Star]:
    """Stars on a regular grid, offset by fractional pixels to test sub-pixel centroids."""
    return [
        Star(x + 0.3, y + 0.6, amplitude, fwhm)
        for y in range(margin, shape[0] - margin + 1, spacing)
        for x in range(margin, shape[1] - margin + 1, spacing)
    ]


def as_preprocessed(
    data: np.ndarray,
    *,
    image_format: ImageFormat = ImageFormat.FITS,
    nominal_max: float | None = None,
) -> PreprocessingResult:
    """Wrap an array as a Milestone 1 preprocessing result (row 0 displayed at top)."""
    metadata = {"raster": {"nominal_max": nominal_max}} if nominal_max is not None else {}
    image = image_from_array(
        data, image_format=image_format, display_origin="upper", metadata=metadata
    )
    return preprocess(image)


def nearest(sources, x: float, y: float):
    """The source closest to (x, y) and its distance."""
    best = min(sources, key=lambda s: (s.x - x) ** 2 + (s.y - y) ** 2)
    return best, float(np.hypot(best.x - x, best.y - y))

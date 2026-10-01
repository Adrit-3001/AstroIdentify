"""Opt-in end-to-end test against a real Astrometry.net installation.

Skipped unless both environment variables point at executables:

* ``ASTROIDENTIFY_SOLVE_FIELD``  - ``solve-field``
* ``ASTROIDENTIFY_BUILD_INDEX``  - ``build-astrometry-index``

The test builds an index from a synthetic star catalogue (arbitrary sky patch, no real
target), renders an 8-bit image of part of it through a known WCS, and runs the full
preprocess -> detect -> blind plate-solve pipeline. It needs no downloaded index data.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.table import Table
from PIL import Image

from astroidentify import preprocess_image
from astroidentify.astrometry import pixel_to_sky, plate_solve
from astroidentify.config import AstrometryConfig
from astroidentify.detection import detect_sources
from tests.astrometry.fixtures import make_truth_wcs

SOLVE_FIELD = os.environ.get("ASTROIDENTIFY_SOLVE_FIELD")
BUILD_INDEX = os.environ.get("ASTROIDENTIFY_BUILD_INDEX")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (SOLVE_FIELD and BUILD_INDEX),
        reason="set ASTROIDENTIFY_SOLVE_FIELD and ASTROIDENTIFY_BUILD_INDEX to run",
    ),
]

W, H = 1000, 800
CENTRE = (150.1, 20.05)


def _index(tmp_path: Path) -> Path:
    rng = np.random.default_rng(42)
    n = 6000
    ra = CENTRE[0] + rng.uniform(-1.0, 1.0, n) / np.cos(np.radians(CENTRE[1]))
    dec = CENTRE[1] + rng.uniform(-1.0, 1.0, n)
    Table({"RA": ra, "Dec": dec, "MAG": rng.uniform(8, 14, n)}).write(tmp_path / "catalog.fits")
    index_dir = tmp_path / "indexes"
    index_dir.mkdir()
    for preset in (2, 3, 4):
        subprocess.run(
            [BUILD_INDEX, "-i", str(tmp_path / "catalog.fits"), "-o",
             str(index_dir / f"index-synth-{preset:02d}.fits"), "-P", str(preset), "-S", "MAG",
             "-E", "-I", f"99000{preset}", "-t", str(tmp_path)],
            check=True, capture_output=True, timeout=300,
        )  # fmt: skip
    return index_dir


def test_blind_solve_of_synthetic_field(tmp_path: Path) -> None:
    index_dir = _index(tmp_path)
    truth = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=2.0, rotation_deg=30.0)
    catalog = Table.read(tmp_path / "catalog.fits")
    x0, y0 = truth.all_world2pix(catalog["RA"], catalog["Dec"], 0)
    rows, cols = np.mgrid[:H, :W].astype(float)
    image = np.random.default_rng(1).normal(20, 3, (H, W))
    sigma = 3.2 / 2.3548
    for x, y, mag in zip(x0, y0, catalog["MAG"], strict=True):
        if -5 < x < W + 5 and -5 < y < H + 5:
            peak = 400 * 10 ** (-0.4 * (mag - 9))
            image += peak * np.exp(-((cols - x) ** 2 + (rows - y) ** 2) / (2 * sigma**2))
    path = tmp_path / "field.png"
    Image.fromarray(np.clip(np.round(image), 0, 255).astype(np.uint8)).save(path)

    detection = detect_sources(preprocess_image(path))
    config = AstrometryConfig(
        solve_field_path=SOLVE_FIELD, index_dirs=(str(index_dir),), cpulimit_seconds=60
    )
    solution = plate_solve(detection, config)
    solution.raise_for_status()

    assert solution.solved and solution.mode == "blind"
    centre = solution.geometry.centre
    error = SkyCoord(*CENTRE, unit="deg").separation(
        SkyCoord(centre.ra_deg, centre.dec_deg, unit="deg")
    )
    assert error.arcsec < 1.0
    assert solution.geometry.pixel_scale_arcsec == pytest.approx(2.0, rel=1e-3)
    assert solution.geometry.up_position_angle_deg == pytest.approx(30.0, abs=0.1)
    assert solution.geometry.parity == "normal"
    # The solved WCS agrees with the truth across the image (canonical coordinates, origin 0).
    gx, gy = np.meshgrid(np.linspace(0, W - 1, 9), np.linspace(0, H - 1, 7))
    ra, dec = pixel_to_sky(solution.wcs, gx.ravel(), gy.ravel())
    ra_t, dec_t = truth.all_pix2world(gx.ravel(), gy.ravel(), 0)
    offsets = SkyCoord(ra, dec, unit="deg").separation(SkyCoord(ra_t, dec_t, unit="deg"))
    assert np.max(offsets.arcsec) < 0.5
    assert solution.match_statistics.n_matched >= 10
    assert solution.match_statistics.median_residual_arcsec < 1.0

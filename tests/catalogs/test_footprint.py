from __future__ import annotations

import numpy as np
import pytest
from astropy.coordinates import SkyCoord

from astroidentify.catalogs.footprint import project_to_image, query_region, validate_wcs
from astroidentify.exceptions import InvalidPlateSolutionError
from tests.astrometry.fixtures import make_truth_wcs
from tests.catalogs.fixtures import CENTRE, SCALE, H, W


def test_query_region_reaches_farthest_corner_plus_margin() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=0.0)
    region = query_region(wcs, W, H, margin_arcsec=30.0)
    half_diagonal_deg = np.hypot(W, H) / 2 * SCALE / 3600
    assert region.centre.ra_deg == pytest.approx(
        CENTRE[0]
    ) and region.centre.dec_deg == pytest.approx(CENTRE[1])
    assert max(region.corner_distances_deg.values()) == pytest.approx(half_diagonal_deg, rel=1e-3)
    assert region.radius_deg == pytest.approx(half_diagonal_deg + 30 / 3600, rel=1e-3)


@pytest.mark.parametrize("rotation", [0.0, 33.6, 90.0, 157.0])
def test_rotated_footprint_is_covered(rotation: float) -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=rotation)
    region = query_region(wcs, W, H, margin_arcsec=0.0)
    # Every image pixel edge point lies inside the cone, whatever the rotation.
    edge = np.concatenate([
        np.column_stack([np.linspace(-0.5, W - 0.5, 40), np.full(40, -0.5)]),
        np.column_stack([np.full(40, W - 0.5), np.linspace(-0.5, H - 0.5, 40)]),
        np.column_stack([np.linspace(-0.5, W - 0.5, 40), np.full(40, H - 0.5)]),
    ])  # fmt: skip
    ra, dec = wcs.all_pix2world(edge[:, 0], edge[:, 1], 0)
    sep = SkyCoord(ra, dec, unit="deg").separation(SkyCoord(*CENTRE, unit="deg")).deg
    assert sep.max() <= region.radius_deg + 1e-9


def test_known_sky_position_projects_to_expected_pixel() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=0.0)
    # CRVAL is the image centre: canonical ((W-1)/2, (H-1)/2), no +1 from XYLS logic.
    x, y, inside = project_to_image(wcs, np.array([CENTRE[0]]), np.array([CENTRE[1]]), W, H)
    assert (x[0], y[0]) == pytest.approx(((W - 1) / 2, (H - 1) / 2), abs=1e-6)
    assert inside[0]


def test_no_axis_flip() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=0.0)
    # Camera parity, north up: north is towards row 0 (smaller y), east towards smaller x.
    north = CENTRE[1] + 50 / 3600
    east = CENTRE[0] + 50 / 3600 / np.cos(np.radians(CENTRE[1]))
    x, y, _ = project_to_image(wcs, np.array([CENTRE[0], east]), np.array([north, CENTRE[1]]), W, H)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    assert y[0] == pytest.approx(cy - 50, abs=0.05) and x[0] == pytest.approx(cx, abs=0.05)
    assert x[1] == pytest.approx(cx - 50, abs=0.05) and y[1] == pytest.approx(cy, abs=0.05)


def test_in_image_bounds_inclusive_of_pixel_edges() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=0.0)
    px = np.array([-0.49, -0.51, W - 0.51, W - 0.49, 10.0, 10.0])
    py = np.array([10.0, 10.0, 10.0, 10.0, H - 0.51, H - 0.49])
    ra, dec = wcs.all_pix2world(px, py, 0)
    _, _, inside = project_to_image(wcs, ra, dec, W, H)
    assert list(inside) == [True, False, True, False, True, False]
    _, _, with_margin = project_to_image(wcs, ra, dec, W, H, margin_px=1.0)
    assert with_margin.all()


def test_non_finite_rows_are_excluded() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE)
    x, _, inside = project_to_image(
        wcs, np.array([np.nan, CENTRE[0]]), np.array([CENTRE[1], np.inf]), W, H
    )
    assert not inside.any() and np.isnan(x).all()


def test_ra_wrap_field() -> None:
    wcs = make_truth_wcs(W, H, centre=(0.02, 10.0), scale_arcsec=SCALE, rotation_deg=10.0)
    region = query_region(wcs, W, H, margin_arcsec=10.0)
    assert region.centre.ra_deg == pytest.approx(0.02)
    ra = np.array([359.99, 0.01, 0.05])  # stars on both sides of RA = 0/360
    x, _, inside = project_to_image(wcs, ra, np.full(3, 10.0), W, H)
    assert inside.all()
    assert x[0] > x[1] > x[2]  # east (larger RA across the wrap) is to the left, continuously


def test_validate_wcs_accepts_good_and_rejects_bad() -> None:
    wcs = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE)
    assert validate_wcs(wcs, W, H).pixel_scale_arcsec == pytest.approx(SCALE, rel=1e-4)
    with pytest.raises(InvalidPlateSolutionError, match="image size"):
        validate_wcs(wcs, 0, H)
    broken = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE)
    broken.wcs.cd = [[0.0, 0.0], [0.0, 0.0]]  # singular: not invertible
    with pytest.raises(InvalidPlateSolutionError):
        validate_wcs(broken, W, H)

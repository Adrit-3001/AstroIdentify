from __future__ import annotations

import numpy as np
import pytest
from astropy.coordinates import SkyCoord

from astroidentify.objects.extent import (
    image_overlap,
    inside_sky_ellipse,
    parse_extent,
    sky_outline,
)
from astroidentify.objects.types import EXTENT_CIRCLE, EXTENT_ELLIPSE, EXTENT_NONE
from tests.objects.fixtures import SCALE, H, W, wcs


def test_parse_extent_rules() -> None:
    assert parse_extent(None, None, None, None).shape == EXTENT_NONE
    assert parse_extent(0.0, 0.0, 10, "A").shape == EXTENT_NONE  # no invented size
    ellipse = parse_extent(4.0, 2.0, 200.0, "B")
    assert ellipse.shape == EXTENT_ELLIPSE and ellipse.position_angle_deg == 20.0  # PA mod 180
    assert parse_extent(4.0, 2.0, None, None).shape == EXTENT_CIRCLE  # no PA: superset circle
    assert parse_extent(4.0, None, 30.0, None).shape == EXTENT_CIRCLE
    assert parse_extent(3.0, 3.0, None, None).shape == EXTENT_ELLIPSE  # round: PA irrelevant
    assert parse_extent(4.0, 2.0, None, None).semi_minor_deg == pytest.approx(4.0 / 120)


def test_sky_outline_follows_position_angle_east_of_north() -> None:
    extent = parse_extent(2.0, 1.0, 90.0, None)  # major axis along east-west
    ra, dec = sky_outline(10.0, 0.0, extent, n=4)  # theta 0, 90, 180, 270 deg
    centre = SkyCoord(10.0, 0.0, unit="deg")
    pa = centre.position_angle(SkyCoord(ra, dec, unit="deg")).deg
    sep = centre.separation(SkyCoord(ra, dec, unit="deg")).arcmin
    np.testing.assert_allclose(pa, [90, 180, 270, 0], atol=1e-6)
    np.testing.assert_allclose(sep, [1.0, 0.5, 1.0, 0.5], atol=1e-6)  # semi-axes
    assert ra[0] > 10.0  # east = increasing RA


def test_inside_sky_ellipse() -> None:
    extent = parse_extent(2.0, 1.0, 0.0, None)  # major axis north-south
    assert inside_sky_ellipse(0.0, 0.0, extent, np.array([0.0]), np.array([0.9 / 60]))[0]
    assert not inside_sky_ellipse(0.0, 0.0, extent, np.array([0.9 / 60]), np.array([0.0]))[0]


def _object_beyond_right_edge(w, offset_px):
    """Sky position ``offset_px`` beyond the right edge, on the middle row."""
    ra, dec = w.all_pix2world([W - 0.5 + offset_px], [(H - 1) / 2], 0)
    return float(ra[0]), float(dec[0])


@pytest.mark.parametrize("rotation", [0.0, 30.0, 135.0])
def test_centre_outside_but_extent_overlaps(rotation) -> None:
    w = wcs(rotation_deg=rotation)
    ra, dec = _object_beyond_right_edge(w, 50)  # 50 px = 100" outside
    big = parse_extent(2 * 150 / 60, 2 * 150 / 60, 0.0, None)  # radius 150" > 100"
    small = parse_extent(2 * 50 / 60, 2 * 50 / 60, 0.0, None)  # radius 50" < 100"
    hit, fraction, outline = image_overlap(w, ra, dec, big, W, H, centre_in_image=False)
    assert hit and 0 < fraction < 0.5 and outline is not None
    assert not image_overlap(w, ra, dec, small, W, H, centre_in_image=False)[0]


def test_footprint_containing_the_whole_image_intersects() -> None:
    w = wcs()
    ra, dec = _object_beyond_right_edge(w, 10)
    huge = parse_extent(120.0, 120.0, 0.0, None)  # 1 deg radius: every outline vertex outside
    hit, fraction, _ = image_overlap(w, ra, dec, huge, W, H, centre_in_image=False)
    # Image area / circle area = (13.3' x 10') / (pi 60'^2) = 1.2%; sampled with 432 points.
    assert hit and 0 < fraction < 0.03


def test_pixel_outline_matches_scale_and_has_no_flip() -> None:
    w = wcs(rotation_deg=0.0)
    ra, dec = w.all_pix2world([200.0], [150.0], 0)
    extent = parse_extent(2 * 40 * SCALE / 60, 2 * 20 * SCALE / 60, 0.0, None)  # 40 x 20 px
    _, fraction, outline = image_overlap(w, ra[0], dec[0], extent, W, H, True)
    xs, ys = np.array(outline).T
    assert fraction == 1.0
    # North-south major axis -> along y (no rotation); semi-axes 40 px (y) and 20 px (x).
    assert ys.max() - ys.min() == pytest.approx(80, abs=0.5)
    assert xs.max() - xs.min() == pytest.approx(40, abs=0.5)
    # Vertex 0 is north (PA 0): row index decreases toward north for camera parity.
    assert outline[0][1] == pytest.approx(150 - 40, abs=0.5) and outline[0][0] == pytest.approx(200)

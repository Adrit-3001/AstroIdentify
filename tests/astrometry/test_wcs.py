from __future__ import annotations

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits

from astroidentify.astrometry.wcs import (
    describe_wcs,
    load_wcs,
    pixel_to_sky,
    sky_to_pixel,
    wcs_from_header,
)
from astroidentify.exceptions import InvalidWCSError
from tests.astrometry.fixtures import HEIGHT, TRUTH_CENTRE, WIDTH, make_truth_wcs, write_header_file


def test_centre_is_the_image_centre() -> None:
    geometry = describe_wcs(make_truth_wcs(), WIDTH, HEIGHT)
    # CRPIX is the FITS centre ((W+1)/2), i.e. canonical ((W-1)/2), so the centre is CRVAL.
    assert geometry.centre.ra_deg == pytest.approx(TRUTH_CENTRE[0], abs=1e-9)
    assert geometry.centre.dec_deg == pytest.approx(TRUTH_CENTRE[1], abs=1e-9)


def test_scale_field_size_and_corners() -> None:
    geometry = describe_wcs(make_truth_wcs(scale_arcsec=1.5), WIDTH, HEIGHT)
    assert geometry.pixel_scale_arcsec == pytest.approx(1.5, rel=1e-4)
    assert geometry.pixel_scale_x_arcsec == pytest.approx(1.5, rel=1e-4)
    assert geometry.field_width_deg * 3600 == pytest.approx(WIDTH * 1.5, rel=1e-3)
    assert geometry.field_height_deg * 3600 == pytest.approx(HEIGHT * 1.5, rel=1e-3)
    centre = SkyCoord(*TRUTH_CENTRE, unit="deg")
    half_diagonal = np.hypot(WIDTH, HEIGHT) / 2 * 1.5
    assert set(geometry.corners) == {"top_left", "top_right", "bottom_right", "bottom_left"}
    for corner in geometry.corners.values():
        separation = centre.separation(SkyCoord(corner.ra_deg, corner.dec_deg, unit="deg"))
        assert separation.arcsec == pytest.approx(half_diagonal, rel=1e-3)


@pytest.mark.parametrize("rotation", [0.0, 25.0, 90.0, 200.0])
def test_orientation_and_normal_parity(rotation: float) -> None:
    geometry = describe_wcs(make_truth_wcs(rotation_deg=rotation), WIDTH, HEIGHT)
    assert geometry.up_position_angle_deg == pytest.approx(rotation % 360, abs=1e-3)
    assert geometry.parity == "normal"


def test_north_up_east_left_camera_image() -> None:
    wcs = make_truth_wcs(rotation_deg=0.0)
    cx, cy = (WIDTH - 1) / 2, (HEIGHT - 1) / 2
    ra_left, _ = pixel_to_sky(wcs, np.array([cx - 50]), np.array([cy]))
    _, dec_top = pixel_to_sky(wcs, np.array([cx]), np.array([cy - 50]))
    assert ra_left[0] > TRUTH_CENTRE[0]  # east (larger RA) is to the left
    assert dec_top[0] > TRUTH_CENTRE[1]  # north is up (towards row 0)


def test_mirrored_parity() -> None:
    geometry = describe_wcs(make_truth_wcs(camera_parity=False), WIDTH, HEIGHT)
    assert geometry.parity == "mirrored"


@pytest.mark.parametrize("camera_parity", [True, False])
def test_pixel_sky_pixel_round_trip(camera_parity: bool) -> None:
    wcs = make_truth_wcs(camera_parity=camera_parity)
    x = np.array([0.0, 0.5, 119.5, 239.0, -0.5, 17.25])
    y = np.array([0.0, 199.0, 99.5, 3.0, -0.5, 150.75])
    ra, dec = pixel_to_sky(wcs, x, y)
    rx, ry = sky_to_pixel(wcs, ra, dec)
    np.testing.assert_allclose(rx, x, atol=1e-7)
    np.testing.assert_allclose(ry, y, atol=1e-7)


def test_explicit_origin_is_zero_based() -> None:
    wcs = make_truth_wcs()
    # FITS CRPIX = (W+1)/2 is canonical (W-1)/2: the canonical centre maps exactly to CRVAL.
    ra, dec = pixel_to_sky(wcs, np.array([(WIDTH - 1) / 2]), np.array([(HEIGHT - 1) / 2]))
    assert (ra[0], dec[0]) == pytest.approx(TRUTH_CENTRE, abs=1e-10)
    # Astropy's origin=1 would be one pixel off for the same numbers.
    ra1, dec1 = wcs.all_pix2world([(WIDTH - 1) / 2], [(HEIGHT - 1) / 2], 1)
    offset = SkyCoord(ra[0], dec[0], unit="deg").separation(SkyCoord(ra1[0], dec1[0], unit="deg"))
    assert offset.arcsec == pytest.approx(1.5 * np.sqrt(2), rel=1e-3)


def test_load_wcs_file(tmp_path) -> None:
    path = write_header_file(make_truth_wcs(), tmp_path / "solution.wcs")
    wcs, header = load_wcs(path)
    assert header["IMAGEW"] == WIDTH
    assert describe_wcs(wcs, WIDTH, HEIGHT).centre.ra_deg == pytest.approx(TRUTH_CENTRE[0])


def test_missing_and_malformed_wcs(tmp_path) -> None:
    with pytest.raises(InvalidWCSError, match="not found"):
        load_wcs(tmp_path / "missing.wcs")
    bad = tmp_path / "bad.wcs"
    bad.write_text("not a FITS header")
    with pytest.raises(InvalidWCSError, match="could not read"):
        load_wcs(bad)
    no_celestial = fits.Header({"NAXIS": 2, "CTYPE1": "LINEAR", "CTYPE2": "LINEAR"})
    with pytest.raises(InvalidWCSError, match="celestial"):
        wcs_from_header(no_celestial)

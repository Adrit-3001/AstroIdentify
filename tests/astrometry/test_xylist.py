from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits

from astroidentify.astrometry.selection import select_from_sources
from astroidentify.astrometry.types import TIER_PREFERRED, TIER_SECONDARY
from astroidentify.astrometry.xylist import (
    ASTROMETRY_NET_PIXEL_OFFSET,
    from_solver_pixels,
    to_solver_pixels,
    write_xylist,
)
from tests.astrometry.test_selection import _src


def test_pixel_offset_is_fits_one_based() -> None:
    # Verified against Astrometry.net 0.93 (see astrometry/xylist.py): canonical 0 -> 1.
    assert ASTROMETRY_NET_PIXEL_OFFSET == 1.0
    assert to_solver_pixels(0.0, 0.0) == (1.0, 1.0)
    assert to_solver_pixels(30.0, 20.0) == (31.0, 21.0)
    assert from_solver_pixels(31.0, 21.0) == (30.0, 20.0)


def test_conversion_round_trip_on_arrays() -> None:
    x = np.array([-0.5, 0.0, 12.25, 2559.5])
    y = np.array([-0.5, 0.0, 7.75, 1919.5])
    sx, sy = to_solver_pixels(x, y)
    np.testing.assert_array_equal(sx, x + 1)
    rx, ry = from_solver_pixels(sx, sy)
    np.testing.assert_array_equal(rx, x)
    np.testing.assert_array_equal(ry, y)


@pytest.fixture
def selection():
    sources = [_src(i, 10.25 * i, 5.5 * i, 1000.0 / i) for i in range(1, 11)]
    return select_from_sources(
        sources,
        width=320,
        height=240,
        max_sources=10,
        min_sources=3,
        allowed_tiers=(TIER_PREFERRED, TIER_SECONDARY),
        grid_shape=(2, 2),
    )


def test_xylist_contents(selection, tmp_path) -> None:
    path = write_xylist(selection, tmp_path / "field.xyls")
    with fits.open(path) as hdul:
        table = hdul[1].data
        header = hdul[1].header
        assert header["IMAGEW"] == 320 and header["IMAGEH"] == 240
        assert hdul[0].header["IMAGEW"] == 320
        assert list(hdul[1].columns.names) == ["X", "Y", "FLUX", "SOURCE_ID"]
        ordered = sorted(selection.sources, key=lambda s: s.rank)
        np.testing.assert_array_equal(table["X"], [s.x + 1 for s in ordered])
        np.testing.assert_array_equal(table["Y"], [s.y + 1 for s in ordered])
        np.testing.assert_array_equal(table["SOURCE_ID"], [s.source_id for s in ordered])
        assert np.all(np.diff(table["FLUX"]) <= 0)  # brightest first


def test_writing_does_not_change_canonical_coordinates(selection, tmp_path) -> None:
    before = [(s.x, s.y) for s in selection.sources]
    write_xylist(selection, tmp_path / "field.xyls")
    assert [(s.x, s.y) for s in selection.sources] == before

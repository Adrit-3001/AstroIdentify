"""Opt-in live SIMBAD test (network). Skipped unless ASTROIDENTIFY_LIVE_SIMBAD=1."""

from __future__ import annotations

import os

import pytest
from astropy.coordinates import SkyCoord

from astroidentify.astrometry.types import SkyPosition
from astroidentify.catalogs.types import QueryRegion
from astroidentify.config import ObjectConfig
from astroidentify.objects.simbad import SIMBAD_COLUMNS, SimbadProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ASTROIDENTIFY_LIVE_SIMBAD") != "1",
        reason="set ASTROIDENTIFY_LIVE_SIMBAD=1 to query the live SIMBAD service",
    ),
]


def test_small_live_cone_query() -> None:
    # Arbitrary position; the test checks the interface, not any particular object.
    region = QueryRegion(SkyPosition(30.0, -40.0), 5.0 / 60, {}, 0.0, 100, 100)
    config = ObjectConfig(network_timeout_seconds=180, max_object_radius_deg=0.2)
    result = SimbadProvider(config).query(region)
    assert result.origin == "live" and len(result.rows) > 0
    assert set(result.rows[0]) == set(SIMBAD_COLUMNS)
    centre = SkyCoord(30.0, -40.0, unit="deg")
    for row in result.rows:
        separation = centre.separation(SkyCoord(row["ra"], row["dec"], unit="deg")).deg
        size = (row["galdim_majaxis"] or 0) / 120
        assert separation <= region.radius_deg + size + 1e-6  # in the cone or reaching it

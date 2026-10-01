"""Opt-in live Gaia DR3 test (network). Skipped unless ASTROIDENTIFY_LIVE_GAIA=1."""

from __future__ import annotations

import os

import numpy as np
import pytest

from astroidentify.astrometry.types import SkyPosition
from astroidentify.catalogs.gaia import GaiaDR3Provider
from astroidentify.catalogs.types import QueryRegion
from astroidentify.config import CatalogConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ASTROIDENTIFY_LIVE_GAIA") != "1",
        reason="set ASTROIDENTIFY_LIVE_GAIA=1 to query the live Gaia archive",
    ),
]


def test_small_live_cone_query() -> None:
    region = QueryRegion(SkyPosition(150.0, 2.0), 1.0 / 60, {}, 0.0, 100, 100)
    result = GaiaDR3Provider(CatalogConfig(network_timeout_seconds=180)).query(region)
    assert result.origin == "live" and not result.truncated
    assert len(result.rows) > 0
    assert result.rows["source_id"].dtype == np.int64
    assert np.all(np.abs(result.rows["dec"] - 2.0) <= 1.0 / 60 + 1e-6)

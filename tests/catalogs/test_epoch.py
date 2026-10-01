from __future__ import annotations

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.time import Time

from astroidentify.catalogs.epoch import propagate, resolve_observation_epoch
from astroidentify.catalogs.types import CatalogTable
from astroidentify.exceptions import ConfigurationError


def _rows(pmra, pmdec):
    n = len(pmra)
    return CatalogTable({
        "source_id": np.arange(n), "ra": np.full(n, 100.0), "dec": np.full(n, 30.0),
        "pmra": np.array(pmra, float), "pmdec": np.array(pmdec, float),
        "ref_epoch": np.full(n, 2016.0),
    })  # fmt: skip


def test_no_timestamp_means_no_propagation() -> None:
    # The filename may contain a date, but only metadata/explicit input is ever used.
    assert resolve_observation_epoch({"raster": {"info": {}}}) == (None, None)
    ra, dec, info = propagate(_rows([1000.0], [0.0]), None, None)
    assert not info.propagated and info.observation_epoch is None
    assert ra[0] == 100.0 and dec[0] == 30.0


@pytest.mark.parametrize(
    ("metadata", "source"),
    [
        ({"fits": {"summary": {"DATE-OBS": "2024-05-01T03:00:00"}}}, "fits_date_obs"),
        ({"fits": {"summary": {"MJD-OBS": 60431.125}}}, "fits_mjd_obs"),
        ({"exif": {"DateTimeOriginal": "2024:05:01 03:00:00"}}, "exif"),
    ],
)
def test_supported_metadata_timestamps(metadata, source) -> None:
    time, found = resolve_observation_epoch(metadata)
    assert found == source and time.datetime.year == 2024 and time.datetime.month == 5


def test_explicit_epoch_wins_and_is_validated() -> None:
    time, source = resolve_observation_epoch(
        {"exif": {"DateTimeOriginal": "2020:01:01 00:00:00"}}, "2026-09-25"
    )
    assert source == "user" and time.datetime.year == 2026
    with pytest.raises(ConfigurationError):
        resolve_observation_epoch({}, "not a date")


def test_high_proper_motion_shift_and_missing_motion() -> None:
    rows = _rows([1000.0, np.nan], [0.0, np.nan])
    ra, dec, info = propagate(rows, Time(2026.0, format="jyear"), "user")
    assert info.propagated and info.n_propagated == 1
    moved = SkyCoord(ra[0], dec[0], unit="deg").separation(SkyCoord(100.0, 30.0, unit="deg")).arcsec
    assert moved == pytest.approx(10.0, rel=1e-3)  # 1000 mas/yr x 10 yr
    assert ra[0] > 100.0  # positive pmra moves east
    assert (ra[1], dec[1]) == (100.0, 30.0)  # no proper motion: catalogue position kept

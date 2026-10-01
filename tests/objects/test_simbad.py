from __future__ import annotations

import pytest

from astroidentify.catalogs.types import QueryRegion
from astroidentify.config import ObjectConfig
from astroidentify.exceptions import (
    CatalogCacheMissError,
    CatalogQueryError,
    CatalogTimeoutError,
    CatalogTruncatedError,
    ConfigurationError,
)
from astroidentify.objects.simbad import SimbadProvider, build_adql, parse_simbad_votable
from tests.catalogs.fixtures import region
from tests.objects.fixtures import row, simbad_votable


class RecordingFetch:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params, timeout):
        self.calls.append((url, params))
        return self.body


def _rows():
    return [
        row(7, 120.51, -12.24, "PN", main_id="NGC  9001", ids="NGC  9001|NAME Test Nebula",
            galdim_majaxis=1.5, galdim_minaxis=1.0, galdim_angle=30.0, galdim_qual="B",
            mag_v=9.5, rvz_redshift=0.0001),
        row(3, 120.49, -12.26, "*"),  # sparse: everything optional missing
    ]  # fmt: skip


def test_adql_is_wcs_derived_structured_and_deterministic() -> None:
    adql = build_adql(region(0.2), 1.2)
    assert adql == build_adql(region(0.2), 1.2)
    for fragment in ("FROM basic AS b", "JOIN ids", "JOIN otypedef", "fb.filter = 'B'",
                     "CIRCLE('ICRS', 120.500000000, -12.250000000, 1.200000000)",
                     "CIRCLE('ICRS', 120.500000000, -12.250000000, 0.200000000)",
                     "b.galdim_majaxis / 120.0 >= DISTANCE", "ORDER BY oid"):  # fmt: skip
        assert fragment in adql
    assert "otype =" not in adql.split("WHERE")[1]  # no type filter at query time


def test_query_parameters_and_parsing() -> None:
    fetch = RecordingFetch(simbad_votable(_rows()))
    result = SimbadProvider(ObjectConfig(row_limit=500), fetch=fetch).query(region())
    url, params = fetch.calls[0]
    assert url.endswith("/sim-tap/sync")
    assert params["LANG"] == "ADQL" and params["MAXREC"] == "500"
    assert [r["oid"] for r in result.rows] == [3, 7]  # ordered by oid
    pn = result.rows[1]
    assert pn["main_id"] == "NGC  9001" and pn["ids"] == "NGC  9001|NAME Test Nebula"
    assert pn["galdim_majaxis"] == pytest.approx(1.5) and pn["galdim_qual"] == "B"
    assert pn["otype_path"] == "* > Ev* > PN"
    sparse = result.rows[0]
    assert sparse["galdim_majaxis"] is None and sparse["mag_v"] is None
    assert sparse["morph_type"] is None and sparse["rvz_redshift"] is None
    assert result.origin == "live" and result.raw_response


def test_rows_without_position_are_dropped_and_duplicates_merged() -> None:
    rows = [*_rows(), row(7, 120.51, -12.24, "PN"), row(9, None, None, "G")]
    parsed, status, _ = parse_simbad_votable(simbad_votable(rows))
    assert [r["oid"] for r in parsed] == [3, 7] and status == "OK"


def test_service_error_and_malformed_responses() -> None:
    with pytest.raises(CatalogQueryError, match="Incorrect ADQL"):
        parse_simbad_votable(simbad_votable(_rows(), status="ERROR", message="Incorrect ADQL"))
    with pytest.raises(CatalogQueryError, match="malformed"):
        parse_simbad_votable(b"<html>gateway error</html>")


def test_overflow_is_an_error_not_a_complete_field() -> None:
    body = simbad_votable(_rows(), status="OVERFLOW")
    with pytest.raises(CatalogTruncatedError):
        SimbadProvider(fetch=RecordingFetch(body)).query(region())
    with pytest.raises(CatalogTruncatedError):  # filling the limit is also truncation
        SimbadProvider(
            ObjectConfig(row_limit=2), fetch=RecordingFetch(simbad_votable(_rows()))
        ).query(region())


def test_network_failure_propagates() -> None:
    def down(url, params, timeout):
        raise CatalogTimeoutError("too slow")

    with pytest.raises(CatalogTimeoutError):
        SimbadProvider(fetch=down).query(region())


def test_cache_miss_hit_refresh_and_offline(tmp_path) -> None:
    body = simbad_votable(_rows())
    fetch = RecordingFetch(body)
    config = ObjectConfig(cache_dir=str(tmp_path))
    first = SimbadProvider(config, fetch=fetch).query(region())
    second = SimbadProvider(config, fetch=fetch).query(region())
    assert (first.origin, second.origin, len(fetch.calls)) == ("live", "cache", 1)
    assert first.cache_key == second.cache_key and second.cache_path
    assert second.rows == first.rows

    offline = ObjectConfig(cache_dir=str(tmp_path), offline=True)
    assert SimbadProvider(offline, fetch=fetch).query(region()).origin == "cache"  # offline hit
    other = QueryRegion(region().centre, 0.3, {}, 30.0, 10, 10)
    with pytest.raises(CatalogCacheMissError):  # offline miss: never an empty field
        SimbadProvider(offline, fetch=fetch).query(other)
    assert len(fetch.calls) == 1

    refreshed = SimbadProvider(ObjectConfig(cache_dir=str(tmp_path), refresh_cache=True),
                               fetch=fetch).query(region())  # fmt: skip
    assert refreshed.origin == "live" and len(fetch.calls) == 2


def test_failed_response_is_not_cached(tmp_path) -> None:
    config = ObjectConfig(cache_dir=str(tmp_path))
    with pytest.raises(CatalogQueryError):
        SimbadProvider(config, fetch=RecordingFetch(b"not xml")).query(region())
    assert not list(tmp_path.iterdir())


def test_cache_key_differs_between_fields(tmp_path) -> None:
    config = ObjectConfig(cache_dir=str(tmp_path))
    fetch = RecordingFetch(simbad_votable(_rows()))
    a = SimbadProvider(config, fetch=fetch).query(region(0.1))
    b = SimbadProvider(config, fetch=fetch).query(region(0.2))
    assert a.cache_key != b.cache_key and len(fetch.calls) == 2


def test_offline_configuration_rules() -> None:
    with pytest.raises(ConfigurationError):
        ObjectConfig(offline=True)  # needs a cache directory
    with pytest.raises(ConfigurationError):
        ObjectConfig(offline=True, refresh_cache=True, cache_dir="x")

from __future__ import annotations

import json
import urllib.error

import numpy as np
import pytest

from astroidentify.catalogs import gaia
from astroidentify.catalogs.gaia import GaiaDR3Provider, build_adql, http_fetch, parse_votable
from astroidentify.config import GAIA_DR3_COLUMNS, CatalogConfig
from astroidentify.exceptions import CatalogQueryError, CatalogTimeoutError, CatalogTruncatedError
from tests.catalogs.fixtures import gaia_columns, region, votable_bytes


class RecordingFetch:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, params: dict, timeout: float) -> bytes:
        self.calls.append((url, params, timeout))
        return self.body


def test_adql_is_a_cone_with_required_columns_and_no_top() -> None:
    adql = build_adql(region(0.25), GAIA_DR3_COLUMNS, "gaiadr3.gaia_source")
    required = (
        "source_id", "ra", "dec", "ra_error", "dec_error", "phot_g_mean_mag",
        "phot_bp_mean_mag", "phot_rp_mean_mag", "pmra", "pmdec", "parallax",
    )  # fmt: skip
    for column in required:
        assert column in adql
    assert "CIRCLE('ICRS', 120.500000000, -12.250000000, 0.250000000)" in adql
    assert "FROM gaiadr3.gaia_source" in adql and "ORDER BY source_id" in adql
    assert "TOP" not in adql.upper()


def test_query_parameters_and_parsing() -> None:
    fetch = RecordingFetch(votable_bytes(gaia_columns(5)))
    config = CatalogConfig(row_limit=1000, network_timeout_seconds=7.0)
    result = GaiaDR3Provider(config, fetch=fetch).query(region())
    url, params, timeout = fetch.calls[0]
    assert url.endswith("/tap/sync") and timeout == 7.0
    assert (
        params["REQUEST"] == "doQuery" and params["LANG"] == "ADQL" and params["MAXREC"] == "1000"
    )
    assert len(result.rows) == 5 and result.origin == "live" and not result.truncated
    assert result.rows["source_id"].dtype == np.int64
    assert result.release == "Gaia DR3"


def test_null_values_become_nan() -> None:
    cols = gaia_columns(3)
    masked = {
        "pmra": np.array([False, True, False]),
        "phot_bp_mean_mag": np.array([True, False, False]),
    }
    table, status, _ = parse_votable(votable_bytes(cols, masked=masked), GAIA_DR3_COLUMNS)
    assert np.isnan(table["pmra"][1]) and np.isfinite(table["pmra"][0])
    assert np.isnan(table["phot_bp_mean_mag"][0])
    assert status == "OK"


def test_empty_result() -> None:
    fetch = RecordingFetch(votable_bytes({k: v[:0] for k, v in gaia_columns(1).items()}))
    assert len(GaiaDR3Provider(fetch=fetch).query(region()).rows) == 0


def test_service_error_status() -> None:
    fetch = RecordingFetch(votable_bytes(gaia_columns(0), status="ERROR", message="bad ADQL"))
    with pytest.raises(CatalogQueryError, match="bad ADQL"):
        GaiaDR3Provider(fetch=fetch).query(region())


def test_overflow_and_limit_reached_are_truncation() -> None:
    with pytest.raises(CatalogTruncatedError):
        GaiaDR3Provider(
            fetch=RecordingFetch(votable_bytes(gaia_columns(3), status="OVERFLOW"))
        ).query(region())
    with pytest.raises(CatalogTruncatedError, match="row limit"):
        GaiaDR3Provider(
            CatalogConfig(row_limit=3), fetch=RecordingFetch(votable_bytes(gaia_columns(3)))
        ).query(region())


def test_malformed_and_incomplete_responses() -> None:
    with pytest.raises(CatalogQueryError, match="malformed"):
        GaiaDR3Provider(fetch=RecordingFetch(b"<html>gateway error</html>")).query(region())
    cols = gaia_columns(2)
    del cols["pmra"]
    with pytest.raises(CatalogQueryError, match="lacks columns"):
        GaiaDR3Provider(fetch=RecordingFetch(votable_bytes(cols))).query(region())


def test_timeout_from_fetch_propagates() -> None:
    def slow(url, params, timeout):
        raise CatalogTimeoutError("too slow")

    with pytest.raises(CatalogTimeoutError):
        GaiaDR3Provider(fetch=slow).query(region())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timed out"), CatalogTimeoutError),
        (urllib.error.URLError(TimeoutError("timed out")), CatalogTimeoutError),
        (urllib.error.URLError("Name or service not known"), CatalogQueryError),
        (urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None), CatalogQueryError),
    ],
)
def test_http_fetch_error_mapping(monkeypatch, error, expected) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(gaia.urllib.request, "urlopen", fail)
    with pytest.raises(expected):
        http_fetch("https://example.invalid/sync", {"QUERY": "x"}, 1.0)


def test_cache_miss_then_hit(tmp_path) -> None:
    fetch = RecordingFetch(votable_bytes(gaia_columns(4)))
    config = CatalogConfig(cache_dir=str(tmp_path))
    first = GaiaDR3Provider(config, fetch=fetch).query(region())
    second = GaiaDR3Provider(config, fetch=fetch).query(region())
    assert first.origin == "live" and second.origin == "cache" and len(fetch.calls) == 1
    assert second.queried_at == first.queried_at and second.query_seconds is None
    assert second.cache_path == first.cache_path
    np.testing.assert_array_equal(first.rows["source_id"], second.rows["source_id"])


def test_cache_is_specific_to_the_query(tmp_path) -> None:
    fetch = RecordingFetch(votable_bytes(gaia_columns(4)))
    config = CatalogConfig(cache_dir=str(tmp_path))
    GaiaDR3Provider(config, fetch=fetch).query(region(0.10))
    GaiaDR3Provider(config, fetch=fetch).query(region(0.11))  # different footprint
    GaiaDR3Provider(CatalogConfig(cache_dir=str(tmp_path), row_limit=500), fetch=fetch).query(
        region(0.10)
    )
    assert len(fetch.calls) == 3


def test_tampered_or_refreshed_cache_is_not_reused(tmp_path) -> None:
    fetch = RecordingFetch(votable_bytes(gaia_columns(4)))
    config = CatalogConfig(cache_dir=str(tmp_path))
    GaiaDR3Provider(config, fetch=fetch).query(region())
    meta = next(tmp_path.glob("*.json"))
    record = json.loads(meta.read_text())
    record["identity"]["adql"] = "SELECT something else"
    meta.write_text(json.dumps(record))
    assert GaiaDR3Provider(config, fetch=fetch).query(region()).origin == "live"
    refreshed = GaiaDR3Provider(
        CatalogConfig(cache_dir=str(tmp_path), refresh_cache=True), fetch=fetch
    )
    assert refreshed.query(region()).origin == "live" and len(fetch.calls) == 3

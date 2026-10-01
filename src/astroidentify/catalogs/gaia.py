"""Gaia DR3 provider: IVOA TAP synchronous ADQL queries against the ESA Gaia archive.

* Protocol: TAP ``/sync`` (``REQUEST=doQuery, LANG=ADQL, FORMAT=votable, MAXREC``), parsed
  with Astropy's VOTable reader. No web scraping and no extra client dependency.
* Region: a cone from :func:`catalogs.footprint.query_region` (derived from the WCS only).
* Completeness: no ``TOP N``; ``MAXREC`` is the explicit ``row_limit``. A TAP
  ``QUERY_STATUS=OVERFLOW`` (or a response that fills the limit) means the field is
  incomplete, and is raised as :class:`CatalogTruncatedError`, never treated as complete.
* Cache: the raw VOTable plus an identity record (service, table, exact ADQL, i.e. centre,
  radius and columns, and row limit). An entry is reused only if its identity matches
  exactly; otherwise it is ignored and re-queried, so another field's data is never reused.
* The network call is an injectable ``fetch(url, params, timeout) -> bytes`` so all logic is
  testable offline.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from astropy.io.votable import parse
from astropy.io.votable.exceptions import VOWarning

from astroidentify.catalogs.types import CatalogQueryResult, CatalogTable, QueryRegion
from astroidentify.config import CatalogConfig
from astroidentify.exceptions import (
    CatalogQueryError,
    CatalogTimeoutError,
    CatalogTruncatedError,
)

logger = logging.getLogger(__name__)

PROVIDER = "ESA Gaia archive (TAP)"
RELEASE = "Gaia DR3"
CACHE_FORMAT_VERSION = 1

Fetch = Callable[[str, dict[str, str], float], bytes]
_INTEGER_COLUMNS = {"source_id"}


def build_adql(region: QueryRegion, columns: tuple[str, ...], table: str) -> str:
    """ADQL cone query; coordinates are formatted with fixed precision for a stable cache key."""
    return (
        f"SELECT {', '.join(columns)} FROM {table} "
        "WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {region.centre.ra_deg:.9f}, {region.centre.dec_deg:.9f}, "
        f"{region.radius_deg:.9f})) "
        "ORDER BY source_id"
    )


def http_fetch(url: str, params: dict[str, str], timeout: float) -> bytes:
    """POST a TAP request (form-encoded) and return the response body."""
    data = urllib.parse.urlencode(params).encode("ascii")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "astroidentify"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(500).decode("utf-8", "replace") if exc.fp else ""
        raise CatalogQueryError(
            f"Gaia TAP service returned HTTP {exc.code}: {body.strip()}"
        ) from exc
    except TimeoutError as exc:
        raise CatalogTimeoutError(f"Gaia query exceeded the {timeout:g} s time limit") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError | socket.timeout):
            raise CatalogTimeoutError(f"Gaia query exceeded the {timeout:g} s time limit") from exc
        raise CatalogQueryError(f"could not reach the Gaia TAP service: {exc.reason}") from exc


class GaiaDR3Provider:
    """Queries Gaia DR3 for a sky region (see module docstring)."""

    def __init__(self, config: CatalogConfig | None = None, fetch: Fetch | None = None) -> None:
        self.config = config or CatalogConfig()
        self.fetch = fetch or http_fetch

    def query(self, region: QueryRegion) -> CatalogQueryResult:
        """Return the catalogue rows inside ``region`` (from cache if an identical query exists).

        Raises:
            CatalogTimeoutError: The request timed out.
            CatalogQueryError: Network/service failure or malformed response.
            CatalogTruncatedError: The response hit ``row_limit``.
        """
        config = self.config
        adql = build_adql(region, config.columns, config.table)
        identity = {
            "format": CACHE_FORMAT_VERSION,
            "service_url": config.tap_url,
            "table": config.table,
            "adql": adql,
            "row_limit": config.row_limit,
        }
        cache = _cache_paths(config.cache_dir, identity)
        if cache is not None and not config.refresh_cache:
            cached = _read_cache(*cache, identity)
            if cached is not None:
                body, queried_at = cached
                logger.info("Gaia DR3 rows loaded from cache %s", cache[0])
                return self._result(region, adql, body, "cache", queried_at, None, cache[0])

        params = {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "votable",
            "QUERY": adql,
            "MAXREC": str(config.row_limit),
        }
        queried_at = datetime.now(UTC).isoformat(timespec="seconds")
        started = time.monotonic()
        body = self.fetch(
            f"{config.tap_url.rstrip('/')}/sync", params, config.network_timeout_seconds
        )
        elapsed = time.monotonic() - started
        result = self._result(region, adql, body, "live", queried_at, elapsed, None)
        if cache is not None:
            _write_cache(*cache, identity, body, queried_at)
            result = _with_cache_path(result, cache[0])
        logger.info("Gaia DR3 query returned %d rows in %.1f s", len(result.rows), elapsed)
        return result

    def _result(
        self,
        region: QueryRegion,
        adql: str,
        body: bytes,
        origin: str,
        queried_at: str,
        seconds: float | None,
        cache_path: Path | None,
    ) -> CatalogQueryResult:
        rows, status, message = parse_votable(body, self.config.columns)
        truncated = status == "OVERFLOW" or len(rows) >= self.config.row_limit
        if truncated:
            raise CatalogTruncatedError(
                f"Gaia response reached the row limit ({self.config.row_limit}); the field is "
                "incomplete. Increase row_limit."
            )
        warnings_out = (f"service message: {message}",) if message else ()
        return CatalogQueryResult(
            provider=PROVIDER,
            release=RELEASE,
            table=self.config.table,
            service_url=self.config.tap_url,
            region=region,
            adql=adql,
            columns=self.config.columns,
            row_limit=self.config.row_limit,
            rows=rows,
            truncated=False,
            origin=origin,
            queried_at=queried_at,
            query_seconds=seconds,
            cache_path=str(cache_path) if cache_path else None,
            warnings=warnings_out,
        )


def parse_votable(
    body: bytes, columns: tuple[str, ...]
) -> tuple[CatalogTable, str | None, str | None]:
    """Parse a TAP VOTable response into a :class:`CatalogTable`.

    Returns ``(table, query_status, status_message)``.

    Raises:
        CatalogQueryError: Unparseable response, ``QUERY_STATUS=ERROR`` or missing columns.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", VOWarning)
            votable = parse(io.BytesIO(body), verify="ignore")
    except Exception as exc:  # the VOTable parser raises many types for malformed input
        snippet = body[:200].decode("utf-8", "replace")
        raise CatalogQueryError(
            f"malformed catalogue response: {exc}; starts with {snippet!r}"
        ) from exc

    status, message = None, None
    for resource in votable.resources:
        for info in resource.infos:
            if info.name == "QUERY_STATUS":
                status = (info.value or "").upper()
                message = (info.content or "").strip() or None
    if status == "ERROR":
        raise CatalogQueryError(f"Gaia TAP query failed: {message or '(no message)'}")

    try:
        table = votable.get_first_table().to_table(use_names_over_ids=True)
    except (IndexError, ValueError) as exc:
        raise CatalogQueryError(f"catalogue response has no result table: {exc}") from exc
    names = {name.lower(): name for name in table.colnames}
    missing = [c for c in columns if c.lower() not in names]
    if missing:
        raise CatalogQueryError(f"catalogue response lacks columns {missing}")

    arrays: dict[str, np.ndarray] = {}
    for column in columns:
        values = table[names[column.lower()]]
        if column in _INTEGER_COLUMNS:
            if np.ma.is_masked(values) and np.ma.count_masked(values):
                raise CatalogQueryError(f"catalogue response has null {column} values")
            arrays[column] = np.asarray(values, dtype=np.int64)
        else:
            arrays[column] = np.ma.filled(np.ma.asarray(values, dtype=float), np.nan)
    return CatalogTable(arrays), status, message


def _cache_paths(cache_dir: str | None, identity: dict) -> tuple[Path, Path] | None:
    if cache_dir is None:
        return None
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    base = Path(cache_dir).expanduser() / f"gaia_dr3-{key}"
    return base.with_suffix(".vot"), base.with_suffix(".json")


def _read_cache(data_path: Path, meta_path: Path, identity: dict) -> tuple[bytes, str] | None:
    if not (data_path.is_file() and meta_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("ignoring unreadable catalogue cache entry %s", meta_path)
        return None
    if meta.get("identity") != identity:
        logger.warning(
            "ignoring catalogue cache entry %s with a different query identity", meta_path
        )
        return None
    return data_path.read_bytes(), meta.get("queried_at", "unknown")


def _write_cache(
    data_path: Path, meta_path: Path, identity: dict, body: bytes, queried_at: str
) -> None:
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_bytes(body)
        meta_path.write_text(
            json.dumps({"identity": identity, "queried_at": queried_at}, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("could not write catalogue cache %s: %s", data_path, exc)


def _with_cache_path(result: CatalogQueryResult, path: Path) -> CatalogQueryResult:
    import dataclasses

    return dataclasses.replace(result, cache_path=str(path))

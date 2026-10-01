"""SIMBAD provider: IVOA TAP synchronous ADQL queries against the CDS SIMBAD service.

* Interface: TAP ``/sync`` (``REQUEST=doQuery, LANG=ADQL, FORMAT=votable, MAXREC``), parsed
  with Astropy's VOTable reader. No HTML scraping and no extra client dependency.
* Tables: ``basic`` (identity, type, position, angular size, redshift, morphology, number
  of references), ``ids`` (all identifiers), ``otypedef`` (type description and
  hierarchy) and ``flux`` (B and V magnitudes), all joined on SIMBAD's object id.
* Region: the WCS-derived cone from ``catalogs.footprint.query_region``, plus any object
  in a wider circle (cone + ``max_object_radius_deg``) whose catalogued semi-major axis
  reaches into the cone, so a large object centred outside the image is still found.
* No type filter at query time: every row is returned and kept, and the type policy is
  applied locally (``objects.filtering``).
* Completeness: ``MAXREC`` is the explicit ``row_limit``. A TAP ``OVERFLOW`` status (or a
  response that fills the limit) raises :class:`CatalogTruncatedError`.
* Cache: shared with Milestone 4 (``catalogs.tap``). The key covers service, exact ADQL
  and row limit. ``offline`` never touches the network, and a miss is
  :class:`CatalogCacheMissError`, never an empty field.
"""

from __future__ import annotations

import io
import logging
import math
import time
import warnings
from datetime import UTC, datetime
from typing import Any

import numpy as np
from astropy.io.votable import parse
from astropy.io.votable.exceptions import VOWarning

from astroidentify.catalogs.tap import (
    Fetch,
    cache_key,
    cache_paths,
    read_cache,
    tap_fetch,
    write_cache,
)
from astroidentify.catalogs.types import QueryRegion
from astroidentify.config import ObjectConfig
from astroidentify.exceptions import (
    CatalogCacheMissError,
    CatalogQueryError,
    CatalogTruncatedError,
)
from astroidentify.objects.types import ObjectQueryResult

logger = logging.getLogger(__name__)

SERVICE = "SIMBAD (CDS) TAP"
TABLE = "basic + ids + otypedef + flux"
CACHE_PREFIX = "simbad"
CACHE_FORMAT_VERSION = 1

#: Output columns (aliases) of the query, in order.
SIMBAD_COLUMNS: tuple[str, ...] = (
    "oid",
    "main_id",
    "otype",
    "otype_description",
    "otype_path",
    "ra",
    "dec",
    "galdim_majaxis",
    "galdim_minaxis",
    "galdim_angle",
    "galdim_qual",
    "morph_type",
    "rvz_redshift",
    "nbref",
    "ids",
    "mag_b",
    "mag_v",
)
_INTEGER_COLUMNS = {"oid", "nbref"}
_FLOAT_COLUMNS = {
    "ra", "dec", "galdim_majaxis", "galdim_minaxis", "galdim_angle", "rvz_redshift",
    "mag_b", "mag_v",
}  # fmt: skip


def build_adql(region: QueryRegion, outer_radius_deg: float) -> str:
    """ADQL for ``region`` (fixed precision so the cache key is stable)."""
    ra, dec, radius = region.centre.ra_deg, region.centre.dec_deg, region.radius_deg
    point = "POINT('ICRS', b.ra, b.dec)"
    return (
        "SELECT b.oid, b.main_id, b.otype, t.description AS otype_description, "
        "t.path AS otype_path, b.ra, b.dec, b.galdim_majaxis, b.galdim_minaxis, "
        "b.galdim_angle, b.galdim_qual, b.morph_type, b.rvz_redshift, b.nbref, i.ids, "
        "fb.flux AS mag_b, fv.flux AS mag_v "
        "FROM basic AS b "
        "LEFT OUTER JOIN ids AS i ON i.oidref = b.oid "
        "LEFT OUTER JOIN otypedef AS t ON t.otype = b.otype "
        "LEFT OUTER JOIN flux AS fb ON fb.oidref = b.oid AND fb.filter = 'B' "
        "LEFT OUTER JOIN flux AS fv ON fv.oidref = b.oid AND fv.filter = 'V' "
        f"WHERE 1 = CONTAINS({point}, CIRCLE('ICRS', {ra:.9f}, {dec:.9f}, "
        f"{outer_radius_deg:.9f})) "
        f"AND (1 = CONTAINS({point}, CIRCLE('ICRS', {ra:.9f}, {dec:.9f}, {radius:.9f})) "
        f"OR b.galdim_majaxis / 120.0 >= DISTANCE({point}, POINT('ICRS', {ra:.9f}, "
        f"{dec:.9f})) - {radius:.9f}) "
        "ORDER BY oid"
    )


class SimbadProvider:
    """Queries SIMBAD for a sky region (see module docstring)."""

    def __init__(self, config: ObjectConfig | None = None, fetch: Fetch | None = None) -> None:
        self.config = config or ObjectConfig()
        self.fetch = fetch or (lambda url, params, timeout: tap_fetch(url, params, timeout,
                                                                      SERVICE))  # fmt: skip

    def query(self, region: QueryRegion) -> ObjectQueryResult:
        """Return every SIMBAD object in ``region`` (from cache if an identical query exists).

        Raises:
            CatalogCacheMissError: ``offline`` and no matching cache entry.
            CatalogTimeoutError, CatalogQueryError: Network or service failure.
            CatalogTruncatedError: The response hit ``row_limit``.
        """
        config = self.config
        outer = region.radius_deg + config.max_object_radius_deg
        adql = build_adql(region, outer)
        identity = {
            "format": CACHE_FORMAT_VERSION,
            "service_url": config.tap_url,
            "adql": adql,
            "row_limit": config.row_limit,
        }
        key = cache_key(identity)
        cache = cache_paths(config.cache_dir, CACHE_PREFIX, identity)
        if cache is not None and not config.refresh_cache:
            cached = read_cache(*cache, identity)
            if cached is not None:
                body, queried_at = cached
                logger.info("SIMBAD rows loaded from cache %s", cache[0])
                return self._result(region, outer, adql, body, "cache", queried_at, None,
                                    key, str(cache[0]))  # fmt: skip
        if config.offline:
            raise CatalogCacheMissError(
                f"offline mode: no cached SIMBAD response for this field in {config.cache_dir} "
                f"(cache key {key}); run once online to populate it"
            )

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
        # Parse before caching so a malformed or truncated response is never cached.
        result = self._result(region, outer, adql, body, "live", queried_at, elapsed, key, None)
        if cache is not None:
            write_cache(*cache, identity, body, queried_at)
            result = _with_cache_path(result, str(cache[0]))
        logger.info("SIMBAD query returned %d rows in %.1f s", len(result.rows), elapsed)
        return result

    def _result(
        self, region, outer, adql, body, origin, queried_at, seconds, key, cache_path
    ) -> ObjectQueryResult:
        rows, status, message = parse_simbad_votable(body)
        if status == "OVERFLOW" or len(rows) >= self.config.row_limit:
            raise CatalogTruncatedError(
                f"SIMBAD response reached the row limit ({self.config.row_limit}); the field is "
                "incomplete. Increase the row limit."
            )
        return ObjectQueryResult(
            service=SERVICE,
            service_url=self.config.tap_url,
            table=TABLE,
            region=region,
            outer_radius_deg=outer,
            adql=adql,
            columns=SIMBAD_COLUMNS,
            row_limit=self.config.row_limit,
            rows=rows,
            origin=origin,
            queried_at=queried_at,
            query_seconds=seconds,
            cache_key=key,
            cache_path=cache_path,
            raw_response=body,
            warnings=(f"service message: {message}",) if message else (),
        )


def parse_simbad_votable(body: bytes) -> tuple[tuple[dict[str, Any], ...], str | None, str | None]:
    """Parse a SIMBAD TAP VOTable into row dicts (missing values -> ``None``).

    Rows are de-duplicated by ``oid`` (first occurrence) and ordered by ``oid``.

    Returns ``(rows, query_status, status_message)``.

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
            f"malformed SIMBAD response: {exc}; starts with {snippet!r}"
        ) from exc

    status, message = None, None
    for resource in votable.resources:
        for info in resource.infos:
            if info.name == "QUERY_STATUS":  # the last one wins (OVERFLOW follows OK)
                status = (info.value or "").upper()
                message = (info.content or "").strip() or None
    if status == "ERROR":
        raise CatalogQueryError(f"SIMBAD TAP query failed: {message or '(no message)'}")
    try:
        table = votable.get_first_table().to_table(use_names_over_ids=True)
    except (IndexError, ValueError) as exc:
        raise CatalogQueryError(f"SIMBAD response has no result table: {exc}") from exc
    names = {name.lower(): name for name in table.colnames}
    missing = [c for c in SIMBAD_COLUMNS if c not in names]
    if missing:
        raise CatalogQueryError(f"SIMBAD response lacks columns {missing}")

    rows: dict[int, dict[str, Any]] = {}
    for record in table:
        row = {column: _value(record[names[column]], column) for column in SIMBAD_COLUMNS}
        if row["oid"] is None or row["ra"] is None or row["dec"] is None:
            continue  # no identity or no position: cannot be placed on the image
        rows.setdefault(row["oid"], row)
    return (
        tuple(rows[k] for k in sorted(rows)),
        status,
        (message if status not in (None, "OK") else None),
    )


def _value(value: Any, column: str) -> Any:
    if value is None or value is np.ma.masked or (np.ma.isMaskedArray(value) and value.mask):
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if column in _INTEGER_COLUMNS:
        return int(value)
    if column in _FLOAT_COLUMNS:
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    return text or None


def _with_cache_path(result: ObjectQueryResult, path: str) -> ObjectQueryResult:
    import dataclasses

    return dataclasses.replace(result, cache_path=path)

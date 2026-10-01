"""Shared IVOA TAP plumbing: synchronous HTTP requests and the on-disk response cache.

Used by every catalogue provider (Gaia DR3 for Milestone 4, SIMBAD for Milestone 5).

Cache contract: an entry is the raw response body plus an identity record (service, table,
exact query, row limit, format version). It is reused only if the stored identity matches
exactly, so a response for another field or configuration is never substituted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from astroidentify.exceptions import CatalogQueryError, CatalogTimeoutError

logger = logging.getLogger(__name__)

#: ``fetch(url, params, timeout) -> body``; injectable so providers are testable offline.
Fetch = Callable[[str, dict[str, str], float], bytes]


def tap_fetch(url: str, params: dict[str, str], timeout: float, service: str) -> bytes:
    """POST a TAP request (form-encoded) and return the response body.

    Raises:
        CatalogTimeoutError: The request timed out.
        CatalogQueryError: HTTP error or the service could not be reached.
    """
    data = urllib.parse.urlencode(params).encode("ascii")
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "astroidentify"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(500).decode("utf-8", "replace") if exc.fp else ""
        raise CatalogQueryError(f"{service} returned HTTP {exc.code}: {body.strip()}") from exc
    except TimeoutError as exc:
        raise CatalogTimeoutError(f"{service} query exceeded the {timeout:g} s time limit") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError | socket.timeout):
            raise CatalogTimeoutError(
                f"{service} query exceeded the {timeout:g} s time limit"
            ) from exc
        raise CatalogQueryError(f"could not reach the {service}: {exc.reason}") from exc


def cache_key(identity: dict) -> str:
    """Deterministic key of a query identity."""
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def cache_paths(cache_dir: str | None, prefix: str, identity: dict) -> tuple[Path, Path] | None:
    """``(response, metadata)`` paths of the cache entry for ``identity`` (``None``: no cache)."""
    if cache_dir is None:
        return None
    base = Path(cache_dir).expanduser() / f"{prefix}-{cache_key(identity)}"
    return base.with_suffix(".vot"), base.with_suffix(".json")


def read_cache(data_path: Path, meta_path: Path, identity: dict) -> tuple[bytes, str] | None:
    """``(body, queried_at)`` of a matching entry, else ``None``."""
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


def write_cache(
    data_path: Path, meta_path: Path, identity: dict, body: bytes, queried_at: str
) -> None:
    """Store an entry; failures are logged, not raised (the live result is still valid)."""
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_bytes(body)
        meta_path.write_text(
            json.dumps({"identity": identity, "queried_at": queried_at}, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("could not write catalogue cache %s: %s", data_path, exc)

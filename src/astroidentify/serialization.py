"""Shared helpers for writing artifacts: strict JSON, NumPy arrays and output directories.

Every milestone's output writer uses these so on-disk conventions stay consistent.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify.exceptions import OutputError


def to_jsonable(value: Any) -> Any:
    """Recursively convert ``value`` to strict-JSON-compatible types.

    NumPy scalars become Python numbers, paths and enums become strings, tuples become
    lists, and non-finite floats become ``None`` (strict JSON has no NaN/Infinity).
    """
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def prepare_output_dir(output_dir: str | Path) -> Path:
    """Create ``output_dir`` if needed and return it.

    Raises:
        OutputError: If the path exists but is not a directory, or cannot be created.
    """
    directory = Path(output_dir).expanduser()
    if directory.exists() and not directory.is_dir():
        raise OutputError(f"output path exists and is not a directory: {directory}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"could not create output directory {directory}: {exc}") from exc
    return directory


def ensure_not_source(targets: Iterable[Path], source: Path) -> None:
    """Raise :class:`OutputError` if any target path is the source image itself."""
    resolved_source = source.resolve()
    for target in targets:
        if target.resolve() == resolved_source:
            raise OutputError(f"refusing to overwrite the source image {resolved_source}")


def sha256_file(path: Path) -> str | None:
    """Content hash of a file for provenance; ``None`` if it is not a regular file."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_array(path: Path, array: np.ndarray) -> None:
    """Save ``array`` as ``.npy`` (no pickling)."""
    try:
        with path.open("wb") as fh:
            np.save(fh, array, allow_pickle=False)
    except OSError as exc:
        raise OutputError(f"could not write {path}: {exc}") from exc


def write_json(path: Path, document: Any) -> None:
    """Write ``document`` as strict, indented JSON (NaN/Infinity are converted to null)."""
    try:
        text = json.dumps(to_jsonable(document), indent=2, allow_nan=False)
        path.write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"could not write {path}: {exc}") from exc


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text, mapping I/O failures to :class:`OutputError`."""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"could not write {path}: {exc}") from exc


def remove_file(path: Path) -> None:
    """Delete ``path``, mapping I/O failures to :class:`OutputError`."""
    try:
        path.unlink()
    except OSError as exc:
        raise OutputError(f"could not remove stale {path}: {exc}") from exc

"""Upload validation: name, size and file signature, before anything is analysed.

The accepted formats are exactly the loader's (``preprocessing.loader.SUPPORTED_EXTENSIONS``).
The file name is only used to pick the format and as a display label; it never becomes a
path on disk.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from pathlib import Path

from astroidentify.preprocessing.loader import SUPPORTED_EXTENSIONS

_FORMATS = (
    ("JPEG", (".jpg", ".jpeg")),
    ("PNG", (".png",)),
    ("FITS", tuple(e for e in SUPPORTED_EXTENSIONS if ".fit" in e or ".fts" in e)),
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_FITS_SIGNATURE = b"SIMPLE  ="
_GZIP_SIGNATURE = b"\x1f\x8b"


class UploadRejected(Exception):
    """An upload that cannot be analysed. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def accepted_formats() -> list[dict]:
    """Formats for the browser: ``[{"name": "JPEG", "extensions": [".jpg", ...]}, ...]``."""
    return [{"name": name, "extensions": list(exts)} for name, exts in _FORMATS]


def extension_of(filename: str) -> str | None:
    """The supported extension of ``filename`` (``.fits.gz`` counts as one), or ``None``."""
    lower = filename.casefold()
    for ext in sorted(SUPPORTED_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return None


def display_name(filename: str) -> str:
    """A safe label for the visitor's file name: base name only, no control characters."""
    name = Path(filename.replace("\\", "/")).name
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "image")[:120]


def check_request(filename: str | None, size: int | None, max_bytes: int) -> str:
    """Validate what the request says before reading the body; return the extension.

    Raises:
        UploadRejected: no file name, unsupported type, missing/zero size, or too large.
    """
    if not filename:
        raise UploadRejected("The upload has no file name.")
    ext = extension_of(filename)
    if ext is None:
        raise UploadRejected(
            f"{display_name(filename)} isn't a supported image. Use a JPEG, PNG or FITS file.",
            status=415,
        )
    if size is None:
        raise UploadRejected("The upload size is missing.", status=411)
    if size <= 0:
        raise UploadRejected("The file is empty.")
    if size > max_bytes:
        raise UploadRejected(
            f"The file is {size / 2**20:.1f} MB; the limit is {max_bytes / 2**20:.0f} MB.",
            status=413,
        )
    return ext


def check_content(path: Path, ext: str) -> None:
    """Check the file's signature matches its extension (cheap; the loader checks fully).

    Raises:
        UploadRejected: the content is not the format the name claims.
    """
    with path.open("rb") as fh:
        head = fh.read(16)
    if ext == ".png":
        ok, kind = head.startswith(_PNG_SIGNATURE), "PNG"
    elif ext in (".jpg", ".jpeg"):
        ok, kind = head.startswith(_JPEG_SIGNATURE), "JPEG"
    elif ext.endswith(".gz"):
        ok, kind = head.startswith(_GZIP_SIGNATURE) and _gzip_is_fits(path), "gzip-compressed FITS"
    else:
        ok, kind = head.startswith(_FITS_SIGNATURE), "FITS"
    if not ok:
        raise UploadRejected(
            f"The file's contents aren't a valid {kind} image, although its name says so.",
            status=415,
        )


def _gzip_is_fits(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as fh:
            return fh.read(len(_FITS_SIGNATURE)) == _FITS_SIGNATURE
    except (OSError, EOFError):
        return False

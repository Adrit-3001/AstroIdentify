"""Astrometry.net source-list (XYLS) export and the pixel-origin conversion.

This module is the **only** place where AstroIdentify's canonical pixel coordinates are
converted to or from Astrometry.net's.

Astrometry.net source lists use FITS pixel coordinates: the centre of the first pixel is
(1, 1), x runs along array columns and y along array rows, with no flip. This was verified
against the installed-version binaries (Astrometry.net 0.93):

* ``image2xy`` (the tool ``solve-field`` uses to build its own source lists) reports a
  synthetic star at 0-based array column 30, row 20 as X = 31.0, Y = 21.0;
* ``solve-field`` solutions of synthetic fields written as canonical + 1 map canonical
  coordinates back to the true sky positions with 0.000 arcsec error through Astropy's
  ``origin=0`` API (and are off by exactly one pixel with ``origin=1``);
* the ``field_x``/``field_y`` columns of its correspondence (``--corr``) output repeat the
  source-list values, so they use the same convention.

Hence: solver = canonical + 1, canonical = solver - 1.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits

from astroidentify.astrometry.types import SourceSelection
from astroidentify.exceptions import OutputError

#: Astrometry.net pixel coordinate of AstroIdentify's canonical pixel 0.
ASTROMETRY_NET_PIXEL_OFFSET = 1.0

X_COLUMN = "X"
Y_COLUMN = "Y"
FLUX_COLUMN = "FLUX"
ID_COLUMN = "SOURCE_ID"


def to_solver_pixels(x: np.ndarray | float, y: np.ndarray | float) -> tuple:
    """Canonical (0-based, pixel-centre) coordinates -> Astrometry.net (FITS 1-based)."""
    return x + ASTROMETRY_NET_PIXEL_OFFSET, y + ASTROMETRY_NET_PIXEL_OFFSET


def from_solver_pixels(x: np.ndarray | float, y: np.ndarray | float) -> tuple:
    """Astrometry.net (FITS 1-based) coordinates -> canonical (0-based, pixel-centre)."""
    return x - ASTROMETRY_NET_PIXEL_OFFSET, y - ASTROMETRY_NET_PIXEL_OFFSET


def write_xylist(selection: SourceSelection, path: Path) -> Path:
    """Write ``selection`` as an Astrometry.net XYLS FITS table.

    Rows are in ``rank`` order (decreasing flux); the solver is also told to sort by the
    ``FLUX`` column, so the order is intentional either way. ``IMAGEW``/``IMAGEH`` record
    the image size, as in the solver's own source lists. ``SOURCE_ID`` is a tag column the
    solver ignores, kept for traceability.
    """
    ordered = sorted(selection.sources, key=lambda s: s.rank)
    x = np.array([s.x for s in ordered], dtype=np.float64)
    y = np.array([s.y for s in ordered], dtype=np.float64)
    solver_x, solver_y = to_solver_pixels(x, y)
    columns = fits.ColDefs(
        [
            fits.Column(name=X_COLUMN, format="D", array=solver_x),
            fits.Column(name=Y_COLUMN, format="D", array=solver_y),
            fits.Column(
                name=FLUX_COLUMN, format="D", array=np.array([s.flux for s in ordered], float)
            ),
            fits.Column(
                name=ID_COLUMN, format="K", array=np.array([s.source_id for s in ordered], int)
            ),
        ]
    )
    table = fits.BinTableHDU.from_columns(columns)
    table.header["IMAGEW"] = (selection.image_width, "image width in pixels")
    table.header["IMAGEH"] = (selection.image_height, "image height in pixels")
    table.header["COMMENT"] = "X, Y are FITS 1-based pixel coordinates (canonical + 1)"
    primary = fits.PrimaryHDU()
    primary.header["IMAGEW"] = selection.image_width
    primary.header["IMAGEH"] = selection.image_height
    try:
        fits.HDUList([primary, table]).writeto(path, overwrite=True)
    except OSError as exc:
        raise OutputError(f"could not write source list {path}: {exc}") from exc
    return path

"""Synthetic SIMBAD rows/responses and WCS for offline Milestone 5 tests.

All objects and sky positions are invented. Nothing refers to the benchmark target.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from astropy.wcs import WCS

from astroidentify.catalogs.types import QueryRegion
from astroidentify.objects.simbad import SIMBAD_COLUMNS
from astroidentify.objects.types import ObjectQueryResult
from tests.astrometry.fixtures import make_truth_wcs
from tests.catalogs.fixtures import votable_bytes

W, H = 400, 300
CENTRE = (150.25, 20.5)  # arbitrary
SCALE = 2.0  # arcsec/pixel

_STRINGS = {"main_id", "otype", "otype_description", "otype_path", "galdim_qual",
            "morph_type", "ids"}  # fmt: skip
_INTS = {"oid", "nbref"}

TYPE_INFO = {  # otype -> (description, SIMBAD hierarchy path)
    "PN": ("Planetary Nebula", "* > Ev* > PN"),
    "G": ("Galaxy", "G"),
    "OpC": ("Open Cluster", "Cl* > OpC"),
    "HII": ("HII Region", "ISM > HII"),
    "*": ("Star", "*"),
    "Rad": ("Radio Source", "Rad"),
    "QSO": ("Quasar", "G > AGN > QSO"),
}


def wcs(centre=CENTRE, rotation_deg: float = 30.0, width: int = W, height: int = H) -> WCS:
    return make_truth_wcs(width, height, centre=centre, scale_arcsec=SCALE,
                          rotation_deg=rotation_deg)  # fmt: skip


def row(oid: int, ra: float, dec: float, otype: str = "G", **values: Any) -> dict[str, Any]:
    """One parsed SIMBAD row (the provider's output format)."""
    description, path = TYPE_INFO.get(otype, (None, None))
    record = {c: None for c in SIMBAD_COLUMNS}
    record.update(oid=oid, main_id=f"OBJ {oid}", otype=otype, otype_description=description,
                  otype_path=path, ra=ra, dec=dec, nbref=10, ids=f"OBJ {oid}")  # fmt: skip
    record.update(values)
    return record


def row_at_pixel(w: WCS, oid: int, x: float, y: float, otype: str = "G", **values) -> dict:
    ra, dec = w.all_pix2world([x], [y], 0)
    return row(oid, float(ra[0]), float(dec[0]), otype, **values)


def simbad_votable(rows: list[dict[str, Any]], status: str = "OK", message=None) -> bytes:
    """A SIMBAD-like TAP VOTable response for ``rows`` (``None`` values are masked)."""
    columns, masked = {}, {}
    for name in SIMBAD_COLUMNS:
        values = [r.get(name) for r in rows]
        mask = np.array([v is None for v in values], bool)
        if name in _STRINGS:
            data = np.array(["" if v is None else str(v) for v in values] or [""], dtype=str)[
                : len(values)
            ]
        elif name in _INTS:
            data = np.array([0 if v is None else int(v) for v in values], dtype=np.int64)
        else:
            data = np.array([np.nan if v is None else float(v) for v in values], float)
        columns[name], masked[name] = data, mask
    return votable_bytes(columns, status=status, message=message, masked=masked)


class FakeObjectProvider:
    """Returns fixed rows for any region (records the regions it was asked for)."""

    def __init__(self, rows: list[dict[str, Any]], origin: str = "live") -> None:
        self.rows = tuple(rows)
        self.origin = origin
        self.regions: list[QueryRegion] = []

    def query(self, region: QueryRegion) -> ObjectQueryResult:
        self.regions.append(region)
        return ObjectQueryResult(
            service="fake", service_url="fake://", table="basic", region=region,
            outer_radius_deg=region.radius_deg + 1.0, adql="SELECT fake", columns=SIMBAD_COLUMNS,
            row_limit=1000, rows=self.rows, origin=self.origin, queried_at="2026-01-01T00:00:00",
            query_seconds=0.0, cache_key="fake", raw_response=simbad_votable(list(self.rows)),
        )  # fmt: skip

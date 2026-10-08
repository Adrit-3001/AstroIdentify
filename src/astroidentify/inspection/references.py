"""Bright reference stars: visual landmarks from saved Milestone 4 Gaia products.

* Source: ``gaia_sources.csv`` (the Gaia rows Milestone 4 retrieved). The positions used are
  the ones Milestone 4 projected (``projected_ra_deg``/``projected_dec_deg``), re-projected
  through the *same WCS as the inspected object*, so stars and object share one geometry.
* Ranking: Gaia G magnitude (brightest first), ties broken by Gaia ``source_id``. Rows
  without G are skipped. Only stars inside the region of interest are counted. The zoom
  view only uses stars at most ``zoom_magnitude_range`` fainter than the faintest
  full-view landmark, so small crops are not filled with faint field stars.
* Labels: a human-readable name only when Milestone 5's saved SIMBAD table already has
  one for a star at that position (within ``name_match_arcsec``). SIMBAD coordinates are
  at epoch J2000 and Gaia DR3's at J2016, so for this comparison only, each Gaia star is
  moved back to J2000 with its own proper motion (when Gaia lists one). Preference: SIMBAD common
  name, then a Bayer/Flamsteed designation (``* ...``), variable-star name (``V* ...``),
  HD, then HIP. Otherwise the star is unnamed (marker only, or its G magnitude if labels
  are requested).

These stars are landmarks only. They are not identification evidence, and Milestone 5's
star-exclusion policy is untouched: excluded SIMBAD rows are only read for names.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from astropy.wcs import WCS

from astroidentify.catalogs.footprint import project_to_image
from astroidentify.exceptions import OutputError


@dataclass(frozen=True)
class ReferenceStar:
    gaia_source_id: int
    g_mag: float
    x: float
    y: float
    name: str | None
    name_source: str | None  # SIMBAD identifier the name came from

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SIMBAD_EPOCH = 2000.0


@dataclass(frozen=True, eq=False)
class GaiaCatalogue:
    source_id: np.ndarray
    g_mag: np.ndarray
    x: np.ndarray
    y: np.ndarray
    # Positions at the SIMBAD epoch, used only to look up names (default: same as x/y).
    x_name: np.ndarray | None = None
    y_name: np.ndarray | None = None


def load_gaia(path: Path, wcs: WCS, width: int, height: int) -> GaiaCatalogue:
    """Read ``gaia_sources.csv`` and project the in-image rows through ``wcs``."""
    try:
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        raise OutputError(f"could not read {path}: {exc}") from exc

    def column(name, kind=float):
        return np.array([kind(r[name]) if r.get(name) not in (None, "") else np.nan for r in rows])

    ids = np.array([int(r["source_id"]) for r in rows], dtype=np.int64)
    ra = column("projected_ra_deg") if rows and "projected_ra_deg" in rows[0] else column("ra")
    dec = column("projected_dec_deg") if rows and "projected_dec_deg" in rows[0] else column("dec")
    g = column("phot_g_mean_mag")
    x, y, inside = project_to_image(wcs, ra, dec, width, height)
    keep = inside & np.isfinite(g)
    ra_name, dec_name = ra.copy(), dec.copy()
    if rows and {"pmra", "pmdec", "ref_epoch"} <= set(rows[0]):
        # Gaia epoch -> SIMBAD epoch (J2000) for name matching; pmra includes cos(dec).
        years = column("ref_epoch") - SIMBAD_EPOCH
        pmra, pmdec = column("pmra"), column("pmdec")
        ok = np.isfinite(years) & np.isfinite(pmra) & np.isfinite(pmdec)
        ra_name[ok] -= pmra[ok] * years[ok] / 3.6e6 / np.cos(np.radians(dec[ok]))
        dec_name[ok] -= pmdec[ok] * years[ok] / 3.6e6
    xn, yn, _ = project_to_image(wcs, ra_name, dec_name, width, height, margin_px=1e9)
    return GaiaCatalogue(ids[keep], g[keep], x[keep], y[keep], xn[keep], yn[keep])


def star_name(record: dict[str, Any]) -> tuple[str, str] | None:
    """``(label, identifier)`` for a SIMBAD star row, by the preference in the docstring."""
    names = list(record.get("common_names") or ())
    if names:
        return names[0], f"NAME {names[0]}"
    aliases = list(record.get("aliases") or ())
    for prefix in ("* ", "V* ", "HD ", "HIP "):
        for alias in aliases:
            if alias.startswith(prefix):
                label = alias[2:] if prefix == "* " else alias[3:] if prefix == "V* " else alias
                return label, alias
    return None


def select_reference_stars(
    gaia: GaiaCatalogue,
    count: int,
    region: tuple[float, float, float, float],
    simbad_stars: list[dict[str, Any]],
    pixel_scale_arcsec: float,
    name_match_arcsec: float = 3.0,
    faintest_g: float | None = None,
) -> list[ReferenceStar]:
    """Brightest ``count`` Gaia stars inside ``region = (x0, y0, x1, y1)`` (canonical px),
    optionally no fainter than ``faintest_g``."""
    if count <= 0 or len(gaia.source_id) == 0:
        return []
    x0, y0, x1, y1 = region
    inside = (gaia.x >= x0) & (gaia.x <= x1) & (gaia.y >= y0) & (gaia.y <= y1)
    if faintest_g is not None:
        inside &= gaia.g_mag <= faintest_g
    index = np.flatnonzero(inside)
    order = index[np.lexsort((gaia.source_id[index], gaia.g_mag[index]))][:count]
    radius_px = name_match_arcsec / pixel_scale_arcsec
    named = [(float(s["projected_x"]), float(s["projected_y"]), star_name(s)) for s in simbad_stars]
    named = [(sx, sy, n) for sx, sy, n in named if n is not None and math.isfinite(sx)]
    stars = []
    for k in order:
        x, y = float(gaia.x[k]), float(gaia.y[k])
        nx = float(gaia.x_name[k]) if gaia.x_name is not None else x
        ny = float(gaia.y_name[k]) if gaia.y_name is not None else y
        best = min(
            ((math.hypot(sx - nx, sy - ny), n) for sx, sy, n in named),
            default=None,
            key=lambda item: item[0],
        )
        name, source = best[1] if best is not None and best[0] <= radius_px else (None, None)
        stars.append(
            ReferenceStar(int(gaia.source_id[k]), float(gaia.g_mag[k]), x, y, name, source)
        )
    return stars

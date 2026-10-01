"""Offline fixtures for catalogue-matching tests: synthetic fields, fake provider, VOTables."""

from __future__ import annotations

import dataclasses
import io
from dataclasses import dataclass

import numpy as np
from astropy.io.votable.tree import Info, VOTableFile
from astropy.table import Table
from astropy.wcs import WCS

from astroidentify.astrometry.types import SkyPosition
from astroidentify.catalogs.types import CatalogQueryResult, CatalogTable, QueryRegion
from astroidentify.config import GAIA_DR3_COLUMNS
from tests.astrometry.fixtures import make_truth_wcs
from tests.detection.test_filtering import _source

W, H = 400, 300
CENTRE = (120.5, -12.25)  # arbitrary sky position (no real target implied)
SCALE = 1.0  # arcsec/pixel


@dataclass
class SyntheticField:
    wcs: WCS
    star_xy: np.ndarray  # canonical pixel positions of the detectable stars
    rows: CatalogTable  # catalogue: detectable stars + fainter background stars


def synthetic_field(
    n_stars: int = 60, n_faint: int = 200, seed: int = 1, wcs: WCS | None = None
) -> SyntheticField:
    """Random stars inside the image with catalogue rows at their exact sky positions."""
    rng = np.random.default_rng(seed)
    wcs = wcs or make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE, rotation_deg=20.0)
    star_xy = np.column_stack([rng.uniform(20, W - 20, n_stars), rng.uniform(20, H - 20, n_stars)])
    faint_xy = np.column_stack(
        [rng.uniform(-40, W + 40, n_faint), rng.uniform(-40, H + 40, n_faint)]
    )
    xy = np.vstack([star_xy, faint_xy])
    ra, dec = wcs.all_pix2world(xy[:, 0], xy[:, 1], 0)
    n = len(xy)
    mags = np.concatenate([rng.uniform(9, 14, n_stars), rng.uniform(17, 21, n_faint)])
    columns = {
        "source_id": np.arange(1_000_000, 1_000_000 + n, dtype=np.int64),
        "ra": ra, "dec": dec,
        "ra_error": np.full(n, 0.02), "dec_error": np.full(n, 0.02),
        "phot_g_mean_mag": mags, "phot_bp_mean_mag": mags + 0.4, "phot_rp_mean_mag": mags - 0.5,
        "pmra": np.zeros(n), "pmdec": np.zeros(n), "parallax": np.full(n, 1.0),
        "ref_epoch": np.full(n, 2016.0),
    }  # fmt: skip
    return SyntheticField(wcs, star_xy, CatalogTable(columns))


def sources_at(xy: np.ndarray, *, start_id: int = 1, saturated=None, accepted: bool = True):
    """Accepted Milestone 2 ``Source`` objects at canonical pixel positions."""
    saturated = np.zeros(len(xy), bool) if saturated is None else np.asarray(saturated)
    return [
        dataclasses.replace(
            _source(),
            source_id=start_id + i,
            x=float(x),
            y=float(y),
            flux=1000.0 - i,
            saturated=bool(saturated[i]),
            accepted=accepted,
            edge=False,
        )
        for i, (x, y) in enumerate(xy)
    ]


class FakeProvider:
    """Catalogue provider returning fixed rows (records the regions it was asked for)."""

    def __init__(self, rows: CatalogTable) -> None:
        self.rows = rows
        self.regions: list[QueryRegion] = []

    def query(self, region: QueryRegion) -> CatalogQueryResult:
        self.regions.append(region)
        return CatalogQueryResult(
            provider="fake", release="Gaia DR3", table="gaiadr3.gaia_source",
            service_url="fake://", region=region, adql="SELECT ...",
            columns=tuple(self.rows.columns), row_limit=100000, rows=self.rows,
            truncated=False, origin="live", queried_at="2026-01-01T00:00:00+00:00",
            query_seconds=0.0,
        )  # fmt: skip


def votable_bytes(
    columns: dict[str, np.ndarray],
    status: str = "OK",
    message: str | None = None,
    masked: dict | None = None,
) -> bytes:
    """A TAP-style VOTable response (QUERY_STATUS INFO + one table)."""
    table = Table(
        {
            k: np.ma.MaskedArray(v, mask=(masked or {}).get(k, np.zeros(len(v), bool)))
            for k, v in columns.items()
        }
    )
    votable = VOTableFile.from_table(table)
    info = Info(name="QUERY_STATUS", value=status)
    if message:
        info.content = message
    votable.resources[0].infos.append(info)
    buffer = io.BytesIO()
    votable.to_xml(buffer)
    return buffer.getvalue()


def gaia_columns(n: int, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    cols = {name: rng.uniform(0, 1, n) for name in GAIA_DR3_COLUMNS}
    cols["source_id"] = np.arange(10, 10 + n, dtype=np.int64)
    cols["ra"] = rng.uniform(120, 121, n)
    cols["dec"] = rng.uniform(-13, -12, n)
    return cols


def region(radius_deg: float = 0.1) -> QueryRegion:
    return QueryRegion(SkyPosition(*CENTRE), radius_deg, {}, 30.0, W, H)

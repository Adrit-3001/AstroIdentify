"""Astrometric match evidence: correspondences, residuals and solver match statistics.

Only raw measurements are reported (counts, residuals, the solver's log-odds). No
percentage "confidence" is derived here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits

from astroidentify.astrometry.types import Correspondence, MatchStatistics, SourceSelection
from astroidentify.astrometry.xylist import from_solver_pixels

logger = logging.getLogger(__name__)

# A correspondence's field position repeats the source-list value exactly (float64), so a
# tiny tolerance suffices to map it back to a selected source.
_SOURCE_MATCH_TOLERANCE_PX = 1e-3
_MATCH_COUNT_COLUMNS = (
    "NMATCH",
    "NDISTRACT",
    "NCONFLICT",
    "NFIELD",
    "NINDEX",
    "QTRIED",
    "QMATCHED",
)


def read_correspondences(path: Path, selection: SourceSelection) -> tuple[Correspondence, ...]:
    """Parse ``solve-field --corr`` output into canonical-coordinate correspondences.

    Returns an empty tuple if the file is missing or unreadable (logged, not fatal: the WCS
    is still valid without it).
    """
    try:
        with fits.open(path) as hdul:
            table = hdul[1].data
            columns = {name.lower() for name in hdul[1].columns.names}
            rows = [tuple(row) for row in table] if table is not None else []
            names = [name.lower() for name in hdul[1].columns.names]
    except (OSError, IndexError, ValueError) as exc:
        logger.warning("could not read correspondences from %s: %s", path, exc)
        return ()
    required = {"field_x", "field_y", "index_x", "index_y", "field_ra", "field_dec"}
    required |= {"index_ra", "index_dec"}
    if not required <= columns or not rows:
        return ()

    data = {name: np.array([row[i] for row in rows], dtype=float) for i, name in enumerate(names)}
    field_x, field_y = from_solver_pixels(data["field_x"], data["field_y"])
    index_x, index_y = from_solver_pixels(data["index_x"], data["index_y"])
    field_sky = SkyCoord(data["field_ra"], data["field_dec"], unit="deg")
    index_sky = SkyCoord(data["index_ra"], data["index_dec"], unit="deg")
    residual_arcsec = field_sky.separation(index_sky).arcsec
    residual_px = np.hypot(field_x - index_x, field_y - index_y)
    weights = data.get("match_weight")

    selected_xy = np.array([(s.x, s.y) for s in selection.sources], dtype=float).reshape(-1, 2)
    selected_ids = [s.source_id for s in selection.sources]
    result = []
    for i in range(len(rows)):
        source_id = None
        if len(selected_xy):
            distance = np.hypot(selected_xy[:, 0] - field_x[i], selected_xy[:, 1] - field_y[i])
            nearest = int(np.argmin(distance))
            if distance[nearest] < _SOURCE_MATCH_TOLERANCE_PX:
                source_id = selected_ids[nearest]
        result.append(
            Correspondence(
                source_id=source_id,
                field_x=float(field_x[i]),
                field_y=float(field_y[i]),
                index_x=float(index_x[i]),
                index_y=float(index_y[i]),
                field_ra_deg=float(data["field_ra"][i]),
                field_dec_deg=float(data["field_dec"][i]),
                index_ra_deg=float(data["index_ra"][i]),
                index_dec_deg=float(data["index_dec"][i]),
                residual_px=float(residual_px[i]),
                residual_arcsec=float(residual_arcsec[i]),
                match_weight=float(weights[i]) if weights is not None else None,
            )
        )
    return tuple(result)


def read_match_file(path: Path | None) -> tuple[float | None, dict[str, int]]:
    """Solver log-odds and verification counts from ``solve-field --match`` output."""
    if path is None:
        return None, {}
    try:
        with fits.open(path) as hdul:
            table = hdul[1].data
            names = set(hdul[1].columns.names)
            if table is None or len(table) == 0:
                return None, {}
            row = table[0]
            log_odds = float(row["LOGODDS"]) if "LOGODDS" in names else None
            counts = {
                name.lower(): int(row[name]) for name in _MATCH_COUNT_COLUMNS if name in names
            }
    except (OSError, IndexError, ValueError, KeyError) as exc:
        logger.warning("could not read match file %s: %s", path, exc)
        return None, {}
    return log_odds, counts


def match_statistics(
    correspondences: tuple[Correspondence, ...],
    n_selected: int,
    log_odds: float | None = None,
    counts: dict[str, int] | None = None,
) -> MatchStatistics:
    """Summarize residuals. ``match_fraction`` = matched selected sources / selected."""
    residual_arcsec = np.array([c.residual_arcsec for c in correspondences], float)
    residual_px = np.array([c.residual_px for c in correspondences], float)
    matched_selected = sum(c.source_id is not None for c in correspondences)

    def stat(values: np.ndarray, func) -> float | None:
        return float(func(values)) if values.size else None

    return MatchStatistics(
        n_matched=len(correspondences),
        n_selected=n_selected,
        match_fraction=matched_selected / n_selected if n_selected else None,
        median_residual_arcsec=stat(residual_arcsec, np.median),
        rms_residual_arcsec=stat(residual_arcsec, lambda v: np.sqrt(np.mean(v**2))),
        max_residual_arcsec=stat(residual_arcsec, np.max),
        median_residual_px=stat(residual_px, np.median),
        rms_residual_px=stat(residual_px, lambda v: np.sqrt(np.mean(v**2))),
        solver_log_odds=log_odds,
        solver_counts=dict(counts or {}),
    )

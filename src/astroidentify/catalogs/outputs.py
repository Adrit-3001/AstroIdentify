"""Writing catalogue-matching artifacts.

Artifacts (in the output directory):

* ``catalog_query.json``         - query provenance (service, release, cone, ADQL, columns,
  row limit/truncation, live vs cache, timestamps, epoch handling);
* ``gaia_sources.csv``           - every returned catalogue row with its projected pixel
  position, in-image/eligible flags and matched detection ID (if any);
* ``catalog_matches.csv``        - one row per one-to-one match;
* ``catalog_match_summary.json`` - inputs provenance, configuration, metrics, WCS
  refinement diagnostics, unmatched detection IDs, warnings, artifact names;
* ``refined_solution.wcs``       - the catalogue-refined WCS (only if refinement ran);
* ``catalog_overlay.png``        - coordinate-exact diagnostic overlay.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from astroidentify import __version__
from astroidentify.catalogs.overlay import RESIDUAL_MAGNIFICATION, render_catalog_overlay
from astroidentify.catalogs.types import CatalogMatch, CatalogMatchResult
from astroidentify.config import CatalogConfig
from astroidentify.detection.types import COORDINATE_CONVENTION
from astroidentify.exceptions import OutputError
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    remove_file,
    write_json,
    write_text,
)
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
QUERY_JSON = "catalog_query.json"
SOURCES_CSV = "gaia_sources.csv"
MATCHES_CSV = "catalog_matches.csv"
SUMMARY_JSON = "catalog_match_summary.json"
REFINED_WCS = "refined_solution.wcs"
OVERLAY_PNG = "catalog_overlay.png"
MATCH_COLUMNS = tuple(f.name for f in fields(CatalogMatch))


@dataclass(frozen=True)
class CatalogOutputPaths:
    """Artifacts written by :func:`save_catalog_outputs` (``None`` if not produced)."""

    directory: Path
    query: Path
    sources: Path
    matches: Path
    summary: Path
    overlay: Path
    refined_wcs: Path | None


def save_catalog_outputs(
    result: CatalogMatchResult,
    image: AstronomyImage,
    output_dir: str | Path,
    config: CatalogConfig,
    provenance: dict[str, Any] | None = None,
) -> CatalogOutputPaths:
    """Write all catalogue-matching artifacts."""
    directory = prepare_output_dir(output_dir)
    paths = CatalogOutputPaths(
        directory=directory,
        query=directory / QUERY_JSON,
        sources=directory / SOURCES_CSV,
        matches=directory / MATCHES_CSV,
        summary=directory / SUMMARY_JSON,
        overlay=directory / OVERLAY_PNG,
        refined_wcs=directory / REFINED_WCS if result.wcs_refined else None,
    )
    names = (QUERY_JSON, SOURCES_CSV, MATCHES_CSV, SUMMARY_JSON, OVERLAY_PNG, REFINED_WCS)
    ensure_not_source([directory / n for n in names], image.source_path)

    write_json(paths.query, query_document(result))
    write_text(paths.sources, catalog_rows_csv(result))
    write_text(paths.matches, matches_csv(result.matches))
    if paths.refined_wcs is not None:
        header = result.wcs_used.to_header(relax=True)
        header["IMAGEW"], header["IMAGEH"] = image.width, image.height
        try:
            # A PrimaryHDU adds SIMPLE/BITPIX/NAXIS, making a valid header-only FITS file
            # (``Header.tofile`` alone omits SIMPLE, which FITS readers reject).
            fits.PrimaryHDU(header=header).writeto(paths.refined_wcs, overwrite=True)
        except OSError as exc:
            raise OutputError(f"could not write {paths.refined_wcs}: {exc}") from exc
    elif (directory / REFINED_WCS).exists():
        remove_file(directory / REFINED_WCS)  # stale artifact from an earlier run
    try:
        render_catalog_overlay(image, result, config.overlay_max_labels).save(
            paths.overlay, format="PNG", compress_level=1
        )
    except OSError as exc:
        raise OutputError(f"could not write {paths.overlay}: {exc}") from exc
    write_json(paths.summary, summary_document(result, config, provenance or {}, paths))
    logger.info("Wrote catalogue outputs to %s", directory)
    return paths


def query_document(result: CatalogMatchResult) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        **result.query.to_dict(),
        "epoch": result.projected.epoch.to_dict(),
    }


def catalog_rows_csv(result: CatalogMatchResult) -> str:
    """Every returned catalogue row plus projection/eligibility/match columns."""
    projected = result.projected
    rows = projected.rows
    matched = {m.gaia_source_id: m.detection_source_id for m in result.matches}
    base = [c for c in result.query.columns if c in rows.columns]
    header = [*base, "projected_ra_deg", "projected_dec_deg", "x_px", "y_px", "in_image",
              "eligible", "matched_detection_source_id"]  # fmt: skip
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for k in range(len(rows)):
        source_id = int(rows["source_id"][k])
        values = [_cell(rows[c][k]) for c in base]
        values += [
            _cell(projected.ra[k]), _cell(projected.dec[k]), _cell(projected.x[k]),
            _cell(projected.y[k]), "true" if projected.in_image[k] else "false",
            "true" if result.eligible_catalog_mask[k] else "false",
            matched.get(source_id, ""),
        ]  # fmt: skip
        writer.writerow(values)
    return buffer.getvalue()


def matches_csv(matches: tuple[CatalogMatch, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(MATCH_COLUMNS)
    for match in matches:
        record = match.to_dict()
        writer.writerow([_cell(record[c]) for c in MATCH_COLUMNS])
    return buffer.getvalue()


def summary_document(
    result: CatalogMatchResult,
    config: CatalogConfig,
    provenance: dict[str, Any],
    paths: CatalogOutputPaths | None = None,
) -> dict[str, Any]:
    refinement = result.refinement
    return {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "statement": "Gaia DR3 sources projected through the solved WCS and associated "
        "one-to-one with detected point sources. This is point-source correspondence, "
        "not object identification.",
        "inputs": provenance,
        "coordinate_convention": COORDINATE_CONVENTION,
        "catalog": {
            "release": result.query.release,
            "table": result.query.table,
            "origin": result.query.origin,
            "queried_at": result.query.queried_at,
            "query_seconds": result.query.query_seconds,
            "cone_radius_deg": result.query.region.radius_deg,
        },
        "matching": {
            "match_radius_arcsec": result.match_radius_arcsec,
            "match_radius_px": result.match_radius_px,
            "pixel_scale_arcsec": result.pixel_scale_arcsec,
            "assignment": "greedy one-to-one by ascending angular separation; ties broken by "
            "Gaia source_id then detection source_id",
            "eligibility": f"brightest {config.brightness_rank_factor} x N_detections in-image "
            "catalogue stars"
            if config.brightness_rank_factor
            else "all in-image catalogue stars",
            "detections": "all accepted Milestone 2 detections",
        },
        "wcs_refinement": _refinement_dict(refinement, result.wcs_refined),
        "epoch": result.projected.epoch.to_dict(),
        "summary": result.summary.to_dict(),
        "unmatched_detection_source_ids": list(result.unmatched_detection_ids),
        "warnings": list(result.warnings),
        "overlay_residual_magnification": RESIDUAL_MAGNIFICATION,
        "config": config.to_dict(),
        "artifacts": _artifacts(paths),
    }


def _refinement_dict(refinement: Any, refined: bool) -> dict[str, Any]:
    if refinement is None:
        return {"applied": False, "reason": "disabled"}
    return {
        "applied": refined,
        "reason": refinement.reason,
        "method": "Astropy fit_wcs_from_points (TAN"
        + ("" if refinement.wcs is None or not refinement.wcs.sip else "-SIP")
        + ") to isolated, mutually nearest, unsaturated detection/catalogue pairs with "
        "iterative sigma clipping; the input (plate-solution) WCS is not modified",
        "registration_pairs": refinement.n_pairs,
        "pairs_used": refinement.n_used,
        "input_wcs_median_offset_arcsec": refinement.input_median_offset_arcsec,
        "input_wcs_mean_offset_px": list(refinement.input_mean_offset_px)
        if refinement.input_mean_offset_px
        else None,
        "refined_fit_median_residual_arcsec": refinement.residual_median_arcsec,
        "refined_fit_rms_residual_arcsec": refinement.residual_rms_arcsec,
    }


def _artifacts(paths: CatalogOutputPaths | None) -> dict[str, str | None]:
    if paths is None:
        return {}
    return {
        f.name: (getattr(paths, f.name).name if getattr(paths, f.name) is not None else None)
        for f in fields(paths)
        if f.name != "directory"
    }


def _cell(value: Any) -> str:
    if isinstance(value, bool | np.bool_):
        return "true" if value else "false"
    if isinstance(value, float | np.floating):
        return "" if not np.isfinite(value) else format(float(value), ".10g")
    if isinstance(value, np.integer):
        return str(int(value))
    return str(value)

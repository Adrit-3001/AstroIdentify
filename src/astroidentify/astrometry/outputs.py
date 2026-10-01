"""Writing plate-solving artifacts.

Artifacts (in the output directory):

* ``selected_sources.csv`` / ``.json`` - the solver source set (canonical coordinates);
* ``source_selection.png``           - overlay of the selection over all accepted detections;
* ``plate_solution.json``            - structured result (solved or not), attempts, constraints;
* ``solver.log``                     - commands, exit status and output of every solver run;
* ``solution.wcs``                   - FITS WCS header (only if solved);
* ``wcs_overlay.png``                - RA/Dec grid overlay (only if solved);
* ``correspondences.csv``            - solver-matched stars and residuals (if available).

Temporary solver files are not copied here.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astroidentify import __version__
from astroidentify.astrometry.overlay import render_selection_overlay, render_wcs_overlay
from astroidentify.astrometry.types import PlateSolution, SourceSelection
from astroidentify.astrometry.xylist import ASTROMETRY_NET_PIXEL_OFFSET
from astroidentify.config import AstrometryConfig
from astroidentify.detection.types import COORDINATE_CONVENTION, DetectionResult
from astroidentify.exceptions import OutputError
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    remove_file,
    sha256_file,
    write_json,
    write_text,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SELECTED_CSV = "selected_sources.csv"
SELECTED_JSON = "selected_sources.json"
SELECTION_PNG = "source_selection.png"
SOLUTION_WCS = "solution.wcs"
SOLUTION_JSON = "plate_solution.json"
WCS_PNG = "wcs_overlay.png"
SOLVER_LOG = "solver.log"
CORRESPONDENCES_CSV = "correspondences.csv"

SELECTED_COLUMNS = (
    "rank",
    "source_id",
    "x",
    "y",
    "flux",
    "snr",
    "saturated",
    "edge",
    "tier",
    "cell",
)
CORRESPONDENCE_COLUMNS = (
    "source_id",
    "field_x",
    "field_y",
    "index_x",
    "index_y",
    "field_ra_deg",
    "field_dec_deg",
    "index_ra_deg",
    "index_dec_deg",
    "residual_px",
    "residual_arcsec",
    "match_weight",
)


@dataclass(frozen=True)
class AstrometryOutputPaths:
    """Artifacts written by :func:`save_astrometry_outputs` (``None`` if not produced)."""

    directory: Path
    selected_csv: Path
    selected_json: Path
    selection_overlay: Path
    plate_solution: Path
    solver_log: Path
    wcs: Path | None
    wcs_overlay: Path | None
    correspondences: Path | None


def save_astrometry_outputs(
    solution: PlateSolution,
    detection: DetectionResult,
    output_dir: str | Path,
    config: AstrometryConfig | None = None,
) -> AstrometryOutputPaths:
    """Write all plate-solving artifacts for ``solution``.

    Stale solved-only artifacts from an earlier run are removed when this run is unsolved,
    so the directory never mixes results.
    """
    config = config or AstrometryConfig()
    directory = prepare_output_dir(output_dir)
    solved = solution.solved and solution.wcs_header is not None
    has_corr = bool(solution.correspondences)
    paths = AstrometryOutputPaths(
        directory=directory,
        selected_csv=directory / SELECTED_CSV,
        selected_json=directory / SELECTED_JSON,
        selection_overlay=directory / SELECTION_PNG,
        plate_solution=directory / SOLUTION_JSON,
        solver_log=directory / SOLVER_LOG,
        wcs=directory / SOLUTION_WCS if solved else None,
        wcs_overlay=directory / WCS_PNG if solved else None,
        correspondences=directory / CORRESPONDENCES_CSV if has_corr else None,
    )
    targets = [
        directory / name
        for name in (
            SELECTED_CSV,
            SELECTED_JSON,
            SELECTION_PNG,
            SOLUTION_JSON,
            SOLVER_LOG,
            SOLUTION_WCS,
            WCS_PNG,
            CORRESPONDENCES_CSV,
        )
    ]
    ensure_not_source(targets, detection.preprocessing.image.source_path)

    selection = solution.selection
    if selection is not None:
        write_text(paths.selected_csv, selection_to_csv(selection))
        write_json(paths.selected_json, selection_document(selection))
        _save_png(
            render_selection_overlay(detection, selection, config.overlay_max_labels),
            paths.selection_overlay,
        )
    write_text(paths.solver_log, solution.solver_log or "(no solver run)\n")

    for name, produced in (
        (SOLUTION_WCS, paths.wcs),
        (WCS_PNG, paths.wcs_overlay),
        (CORRESPONDENCES_CSV, paths.correspondences),
    ):
        stale = directory / name
        if produced is None and stale.exists():
            remove_file(stale)
    if paths.wcs is not None and solution.wcs_header is not None:
        try:
            solution.wcs_header.tofile(paths.wcs, overwrite=True)
        except OSError as exc:
            raise OutputError(f"could not write {paths.wcs}: {exc}") from exc
        _save_png(render_wcs_overlay(detection, solution), paths.wcs_overlay)
    if paths.correspondences is not None:
        write_text(paths.correspondences, correspondences_to_csv(solution))

    write_json(paths.plate_solution, plate_solution_document(solution, detection, config, paths))
    logger.info("Wrote astrometry outputs to %s", directory)
    return paths


def selection_to_csv(selection: SourceSelection) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SELECTED_COLUMNS)
    for s in sorted(selection.sources, key=lambda s: s.rank):
        writer.writerow(
            [s.rank, s.source_id, repr(s.x), repr(s.y), repr(s.flux), repr(s.snr),
             str(s.saturated).lower(), str(s.edge).lower(), s.tier, f"{s.cell[0]};{s.cell[1]}"]
        )  # fmt: skip
    return buffer.getvalue()


def selection_document(selection: SourceSelection) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "coordinate_convention": COORDINATE_CONVENTION,
        "solver_coordinate_note": (
            f"the solver source list uses x + {ASTROMETRY_NET_PIXEL_OFFSET:g}, "
            f"y + {ASTROMETRY_NET_PIXEL_OFFSET:g} (FITS 1-based); values here are canonical"
        ),
        "summary": selection_summary(selection),
        "sources": [s.to_dict() for s in sorted(selection.sources, key=lambda s: s.rank)],
    }


def selection_summary(selection: SourceSelection) -> dict[str, Any]:
    return {
        "n_selected": len(selection),
        "max_sources": selection.max_sources,
        "allowed_tiers": list(selection.allowed_tiers),
        "tier_mode": selection.tier_mode,
        "n_selected_by_tier": selection.n_by_tier,
        "n_candidates_by_tier": selection.n_candidates_by_tier,
        "grid_shape_columns_rows": list(selection.grid_shape),
        "occupied_grid_cells": selection.occupied_cells,
        "image_width": selection.image_width,
        "image_height": selection.image_height,
        "ordering": "rank 1 = highest flux (the order sent to the solver)",
        "warnings": list(selection.warnings),
    }


def correspondences_to_csv(solution: PlateSolution) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CORRESPONDENCE_COLUMNS)
    for match in solution.correspondences:
        record = match.to_dict()
        writer.writerow(["" if record[c] is None else record[c] for c in CORRESPONDENCE_COLUMNS])
    return buffer.getvalue()


def plate_solution_document(
    solution: PlateSolution,
    detection: DetectionResult,
    config: AstrometryConfig,
    paths: AstrometryOutputPaths | None = None,
) -> dict[str, Any]:
    image = detection.preprocessing.image
    selection = solution.selection
    return {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "solved": solution.solved,
        "status": solution.status,
        "error": solution.error,
        "backend": solution.backend,
        "backend_version": solution.backend_version,
        "mode": solution.mode,
        "constraints": solution.constraints,
        "hints_used": {
            "sky_position": False,
            "object_name": False,
            "filename": False,
            "note": "the solver receives only pixel positions, fluxes and image size "
            "(plus pixel-scale bounds in scale-constrained mode)",
        },
        "input": {
            "source_sha256": sha256_file(image.source_path),
            "width": image.width,
            "height": image.height,
        },
        "coordinate_convention": COORDINATE_CONVENTION,
        "wcs_pixel_origin": "Astropy origin=0 with canonical coordinates; the FITS header "
        "itself uses 1-based CRPIX",
        "centre": solution.geometry.centre.to_dict() if solution.geometry else None,
        "geometry": solution.geometry.to_dict() if solution.geometry else None,
        "selected_sources": len(selection) if selection is not None else 0,
        "selection": selection_summary(selection) if selection is not None else None,
        "match_statistics": solution.match_statistics.to_dict()
        if solution.match_statistics
        else None,
        "solver_report": solution.solver_report,
        "attempts": [a.to_dict() for a in solution.attempts],
        "solved_attempt": solution.solved_attempt,
        "runtime_seconds": solution.runtime_seconds,
        "detection_summary": {
            "n_accepted": detection.diagnostics.get("n_accepted"),
            "fwhm_px": detection.fwhm.value,
        },
        "warnings": list(solution.warnings),
        "config": config.to_dict(),
        "artifacts": _artifact_names(paths),
    }


def _artifact_names(paths: AstrometryOutputPaths | None) -> dict[str, str | None]:
    if paths is None:
        return {}
    names = {}
    for name in (
        "selected_csv",
        "selected_json",
        "selection_overlay",
        "plate_solution",
        "solver_log",
        "wcs",
        "wcs_overlay",
        "correspondences",
    ):
        value = getattr(paths, name)
        names[name] = value.name if value is not None else None
    return names


def _save_png(image: Any, path: Path) -> None:
    try:
        image.save(path, format="PNG", compress_level=1)
    except OSError as exc:
        raise OutputError(f"could not write {path}: {exc}") from exc

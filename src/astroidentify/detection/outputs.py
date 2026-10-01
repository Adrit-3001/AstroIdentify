"""Writing detection artifacts.

Artifacts written to the output directory:

* ``sources.csv`` / ``sources.json`` - every candidate (accepted and rejected), brightest
  first, with measurements, flags and rejection reasons.
* ``detection_metadata.json``        - counts, statistics, FWHM estimate, config, warnings.
* ``preprocessing_metadata.json``    - the Milestone 1 metadata of the input (provenance).
* ``detected_sources.png``           - full-resolution diagnostic overlay.
* ``background_map.npy`` / ``background_rms.npy`` - float32 local background and RMS maps,
  plus ``.png`` visualizations of both.

The normalized array and detection plane are not written (both are derivable), to avoid
duplicating large arrays.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from astroidentify import __version__
from astroidentify.detection.filtering import REJECTION_REASONS
from astroidentify.detection.overlay import render_map, render_overlay
from astroidentify.detection.types import (
    COORDINATE_CONVENTION,
    SOURCE_FIELDS,
    DetectionResult,
    Source,
)
from astroidentify.exceptions import OutputError
from astroidentify.preprocessing.outputs import build_metadata
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    save_array,
    sha256_file,
    write_json,
    write_text,
)

logger = logging.getLogger(__name__)

DETECTION_SCHEMA_VERSION = 1
SOURCES_CSV = "sources.csv"
SOURCES_JSON = "sources.json"
DETECTION_METADATA = "detection_metadata.json"
PREPROCESSING_METADATA = "preprocessing_metadata.json"
OVERLAY = "detected_sources.png"
BACKGROUND_MAP = "background_map.npy"
BACKGROUND_RMS = "background_rms.npy"
BACKGROUND_MAP_PNG = "background_map.png"
BACKGROUND_RMS_PNG = "background_rms.png"


@dataclass(frozen=True)
class DetectionOutputPaths:
    """Locations of the artifacts written by :func:`save_detection_outputs`."""

    directory: Path
    sources_csv: Path
    sources_json: Path
    metadata: Path
    preprocessing_metadata: Path
    overlay: Path
    background_map: Path
    background_rms: Path
    background_map_png: Path
    background_rms_png: Path

    def all(self) -> tuple[Path, ...]:
        return tuple(getattr(self, f.name) for f in fields(self) if f.name != "directory")


def save_detection_outputs(result: DetectionResult, output_dir: str | Path) -> DetectionOutputPaths:
    """Write all detection artifacts for ``result`` into ``output_dir``.

    Raises:
        OutputError: If the directory or any artifact cannot be written, or an artifact
            would overwrite the source image.
    """
    directory = prepare_output_dir(output_dir)
    paths = DetectionOutputPaths(
        directory=directory,
        sources_csv=directory / SOURCES_CSV,
        sources_json=directory / SOURCES_JSON,
        metadata=directory / DETECTION_METADATA,
        preprocessing_metadata=directory / PREPROCESSING_METADATA,
        overlay=directory / OVERLAY,
        background_map=directory / BACKGROUND_MAP,
        background_rms=directory / BACKGROUND_RMS,
        background_map_png=directory / BACKGROUND_MAP_PNG,
        background_rms_png=directory / BACKGROUND_RMS_PNG,
    )
    ensure_not_source(paths.all(), result.preprocessing.image.source_path)

    write_text(paths.sources_csv, sources_to_csv(result.sources))
    write_json(paths.sources_json, sources_document(result))
    save_array(paths.background_map, result.background.background)
    save_array(paths.background_rms, result.background.rms)
    _save_png(render_map(result.background.background), paths.background_map_png)
    _save_png(render_map(result.background.rms), paths.background_rms_png)
    _save_png(render_overlay(result), paths.overlay)
    write_json(paths.preprocessing_metadata, build_metadata(result.preprocessing))
    write_json(paths.metadata, build_detection_metadata(result, paths))
    logger.info("Wrote detection outputs to %s", directory)
    return paths


CSV_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(Source))


def sources_to_csv(sources: tuple[Source, ...] | list[Source]) -> str:
    """Render sources as CSV (``rejection_reasons`` joined with ``;``, NaN as empty)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for source in sources:
        record = source.to_dict()
        writer.writerow(_csv_value(record[name]) for name in CSV_COLUMNS)
    return buffer.getvalue()


def sources_document(result: DetectionResult) -> dict[str, Any]:
    """The ``sources.json`` document."""
    return {
        "schema_version": DETECTION_SCHEMA_VERSION,
        "source": result.preprocessing.image.source_path.name,
        "coordinate_convention": COORDINATE_CONVENTION,
        "units": "detection-plane units (source pixel values; channel mean for colour)",
        "fields": SOURCE_FIELDS,
        "fwhm": result.fwhm.value,
        "aperture_radius": result.aperture_radius,
        "sources": [s.to_dict() for s in result.sources],
    }


def build_detection_metadata(
    result: DetectionResult, paths: DetectionOutputPaths | None = None
) -> dict[str, Any]:
    """Summary diagnostics for a detection run."""
    image = result.preprocessing.image
    background = result.background
    return {
        "schema_version": DETECTION_SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "source": image.source_path.name,
        "source_path": str(image.source_path),
        "source_sha256": sha256_file(image.source_path),
        "format": image.format.value,
        "width": image.width,
        "height": image.height,
        "channels": image.channels,
        "coordinate_convention": COORDINATE_CONVENTION,
        "detection_plane": {
            "method": result.plane.method,
            "definition": "mean of colour channels" if image.is_color else "image as loaded",
            "units": "source pixel values",
            "n_invalid_pixels": int(result.plane.invalid_mask.sum()),
            "invalid_pixel_fill": result.plane.fill_value,
        },
        "background_model": {
            "method": "photutils Background2D: sigma-clipped median per tile, "
            "std RMS, median-filtered tile grid",
            "box_size": list(background.box_size),
            "filter_size": background.filter_size,
            "rms_floor": background.rms_floor,
            "background_map": result.diagnostics["background_map"],
            "background_rms_map": result.diagnostics["background_rms_map"],
        },
        "fwhm": {
            "value": result.fwhm.value,
            "method": result.fwhm.method,
            "n_stars": result.fwhm.n_stars,
            "iterations": result.fwhm.iterations,
            "spread_16_84": list(result.fwhm.spread) if result.fwhm.spread else None,
        },
        "detector": {
            "algorithm": "photutils DAOStarFinder (DAOFIND)",
            "threshold": f"{result.config.detection_sigma} x local RMS map",
            "aperture_radius": result.aperture_radius,
        },
        "saturation": {"level": result.saturation_level, "source": result.saturation_source},
        "overlay": {
            "layout": "image pixels at rows 0..height-1 exactly as in the array (no resize, "
            "crop or flip); legend strip appended below",
            "markers": {
                "accepted": "green circle, radius = aperture radius (cyan if edge-flagged)",
                "rejected": "small red circle",
                "saturated": "extra yellow ring",
            },
        },
        "summary": {
            k: v
            for k, v in result.diagnostics.items()
            if k not in ("warnings", "astrometric_centroid")
        },
        "astrometric_centroid": result.diagnostics.get("astrometric_centroid"),
        "rejection_reasons": REJECTION_REASONS,
        "warnings": list(result.warnings),
        "config": result.config.to_dict(),
        "artifacts": {name: path.name for name, path in _artifacts(paths)},
    }


def load_sources(path: str | Path) -> list[Source]:
    """Read a ``sources.json`` written by :func:`save_detection_outputs` back into ``Source``s.

    JSON ``null`` (written for NaN) becomes NaN again for float fields.

    Raises:
        OutputError: The file is missing or not a valid sources document.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        records = document["sources"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OutputError(f"could not read detection sources from {path}: {exc}") from exc
    float_fields = {f.name for f in fields(Source) if f.type in ("float", float)}
    sources = []
    for record in records:
        values = dict(record)
        for name in float_fields:
            if values.get(name) is None:
                values[name] = float("nan")
        values["rejection_reasons"] = tuple(values.get("rejection_reasons") or ())
        if values.get("astrometric_method", "detection") == "detection":
            # Same as x/y by definition; keep it unset (see Source.astrometric_x).
            values["astrometric_x"] = values["astrometric_y"] = None
        try:
            sources.append(
                Source(**{f.name: values[f.name] for f in fields(Source) if f.name in values})
            )
        except TypeError as exc:
            raise OutputError(f"malformed source record in {path}: {exc}") from exc
    return sources


def _artifacts(paths: DetectionOutputPaths | None) -> list[tuple[str, Path]]:
    if paths is None:
        return []
    return [(f.name, getattr(paths, f.name)) for f in fields(paths) if f.name != "directory"]


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "" if value != value else format(value, ".10g")  # NaN -> empty
    if isinstance(value, tuple | list):
        return ";".join(value)
    return str(value)


def _save_png(image: Any, path: Path) -> None:
    try:
        image.save(path, format="PNG", compress_level=1)
    except OSError as exc:
        raise OutputError(f"could not write {path}: {exc}") from exc

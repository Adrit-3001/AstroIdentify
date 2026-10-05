"""Writing evidence artifacts.

* ``object_evidence.csv``   - one row per assessed object: identity, path, key raw and
  normalized features, grades, ambiguity, support level, reason codes, explanation;
* ``object_evidence.json``  - full typed records (all features, missing fields, caps,
  competitors, provenance) with the evidence version;
* ``evidence_summary.json`` - counts by level/path, field astrometry, reason-code glossary,
  rules, configuration, provenance, warnings, artifact names;
* ``evidence_overlay.png``  - retained objects coloured by support level.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from astroidentify import __version__
from astroidentify.config import EvidenceConfig
from astroidentify.detection.types import COORDINATE_CONVENTION
from astroidentify.evidence.explanations import REASON_CODES
from astroidentify.evidence.overlay import render_evidence_overlay
from astroidentify.evidence.types import (
    EVIDENCE_VERSION,
    SUPPORT_LEVELS,
    EvidenceResult,
    ObjectEvidence,
)
from astroidentify.exceptions import OutputError
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    write_json,
    write_text,
)
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
EVIDENCE_CSV = "object_evidence.csv"
EVIDENCE_JSON = "object_evidence.json"
SUMMARY_JSON = "evidence_summary.json"
OVERLAY_PNG = "evidence_overlay.png"

STATEMENT = (
    "Support levels are ordinal, rule-based summaries of explicit evidence. They are not "
    "probabilities, and no calibrated confidence is implied. 'catalogue-only' means the "
    "catalogue and WCS place the object here but the image adds no support; 'insufficient' "
    "is an abstention."
)

CSV_COLUMNS = (
    "catalogue_id",
    "display_name",
    "object_type",
    "category",
    "path",
    "projected_x",
    "projected_y",
    "support_level",
    "image_grade",
    "astrometry_grade",
    "r50_px",
    "r50_source",
    "separation_arcsec",
    "normalized_offset",
    "chance_expectation",
    "detection_source_id",
    "visible_fraction",
    "visibility",
    "contrast",
    "contrast_significance",
    "structure_offset_fraction",
    "placement_ratio",
    "ambiguity",
    "competitors",
    "caps",
    "missing",
    "reason_codes",
    "explanation",
)


@dataclass(frozen=True)
class EvidenceOutputPaths:
    directory: Path
    csv: Path
    json: Path
    summary: Path
    overlay: Path


def save_evidence_outputs(
    result: EvidenceResult,
    image: AstronomyImage,
    output_dir: str | Path,
    config: EvidenceConfig,
    footprints: dict[int, list[tuple[float, float]]],
) -> EvidenceOutputPaths:
    directory = prepare_output_dir(output_dir)
    paths = EvidenceOutputPaths(
        directory,
        directory / EVIDENCE_CSV,
        directory / EVIDENCE_JSON,
        directory / SUMMARY_JSON,
        directory / OVERLAY_PNG,
    )
    ensure_not_source([paths.csv, paths.json, paths.summary, paths.overlay], image.source_path)
    write_text(paths.csv, evidence_csv(result.objects))
    write_json(
        paths.json,
        {
            "schema_version": SCHEMA_VERSION,
            "evidence_version": EVIDENCE_VERSION,
            "statement": STATEMENT,
            "coordinate_convention": COORDINATE_CONVENTION,
            "objects": [o.to_dict() for o in result.objects],
        },
    )
    overlay, drawn = render_evidence_overlay(
        image, result, footprints, config.overlay_max_objects, config.overlay_max_labels
    )
    try:
        overlay.save(paths.overlay, format="PNG", compress_level=1)
    except OSError as exc:
        raise OutputError(f"could not write {paths.overlay}: {exc}") from exc
    write_json(paths.summary, summary_document(result, config, paths, len(drawn)))
    logger.info("Wrote evidence outputs to %s", directory)
    return paths


def _row(o: ObjectEvidence) -> dict[str, Any]:
    c, x = o.compact, o.extended
    return {
        "catalogue_id": o.catalogue_id,
        "display_name": o.display_name,
        "object_type": o.object_type,
        "category": o.category,
        "path": o.path,
        "projected_x": o.projected_x,
        "projected_y": o.projected_y,
        "support_level": o.support_level,
        "image_grade": o.image_grade,
        "astrometry_grade": o.astrometry.grade,
        "r50_px": o.astrometry.r50_px,
        "r50_source": o.astrometry.r50_source,
        "separation_arcsec": c.separation_arcsec if c else None,
        "normalized_offset": c.normalized_offset if c else None,
        "chance_expectation": c.chance_expectation if c else None,
        "detection_source_id": c.detection_source_id if c else None,
        "visible_fraction": x.visible_fraction if x else None,
        "visibility": x.visibility if x else None,
        "contrast": x.contrast if x else None,
        "contrast_significance": x.contrast_significance if x else None,
        "structure_offset_fraction": x.structure_offset_fraction if x else None,
        "placement_ratio": x.placement_ratio if x else None,
        "ambiguity": o.ambiguity.severity,
        "competitors": ";".join(str(k.catalogue_id) for k in o.ambiguity.competitors),
        "caps": ";".join(o.caps),
        "missing": ";".join(o.missing),
        "reason_codes": ";".join(o.reason_codes),
        "explanation": o.explanation,
    }


def evidence_csv(objects: tuple[ObjectEvidence, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for obj in objects:
        row = _row(obj)
        writer.writerow([_cell(row[c]) for c in CSV_COLUMNS])
    return buffer.getvalue()


def summary_document(
    result: EvidenceResult,
    config: EvidenceConfig,
    paths: EvidenceOutputPaths | None,
    n_drawn: int | None,
) -> dict[str, Any]:
    objects = result.objects
    codes = Counter(code for o in objects for code in o.reason_codes)
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "statement": STATEMENT,
        "evidence_score": "not produced: support levels are rule-based and uncalibrated",
        "provenance": result.provenance,
        "image": {
            "width": result.image_width,
            "height": result.image_height,
            "pixel_scale_arcsec": result.pixel_scale_arcsec,
        },
        "field_astrometry": asdict(result.field_astrometry),
        "counts": {
            "assessed": len(objects),
            "by_support_level": result.counts(),
            "by_path": dict(sorted(Counter(o.path for o in objects).items())),
            "by_ambiguity": dict(sorted(Counter(o.ambiguity.severity for o in objects).items())),
            "reason_codes": dict(sorted(codes.items())),
            "drawn_on_overlay": n_drawn,
        },
        "objects": [
            {
                "catalogue_id": o.catalogue_id,
                "display_name": o.display_name,
                "object_type": o.object_type,
                "support_level": o.support_level,
                "reason_codes": list(o.reason_codes),
                "explanation": o.explanation,
            }
            for o in sorted(objects, key=lambda o: SUPPORT_LEVELS.index(o.support_level))[:50]
        ],
        "objects_note": "the 50 best-supported objects; all are in object_evidence.json",
        "reason_code_glossary": REASON_CODES,
        "support_levels": list(SUPPORT_LEVELS),
        "warnings": list(result.warnings),
        "config": config.to_dict(),
        "artifacts": {
            f.name: getattr(paths, f.name).name for f in fields(paths) if f.name != "directory"
        }
        if paths
        else {},
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "" if value != value else format(value, ".6g")
    return str(value)

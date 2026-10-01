"""Writing object-identification artifacts.

Artifacts (in the output directory):

* ``object_query.json``          - query provenance (service, cone, outer radius, ADQL,
  columns, row limit, live/cache, cache key/path, timestamps);
* ``simbad_response.vot``        - the raw SIMBAD response (offline replay/provenance);
* ``catalog_objects.csv``        - every returned row: identity, type/category, position,
  projection, field status, extent, photometry, status and exclusion reason;
* ``catalog_objects.json``       - the same plus aliases and projected outlines;
* ``object_associations.csv``    - image evidence for the retained (in-field) objects;
* ``identification_summary.json``- inputs, WCS used, query summary, counts, retained objects,
  policy, warnings, artifact names;
* ``object_overlay.png``         - the annotated image.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from astroidentify import __version__
from astroidentify.config import ObjectConfig
from astroidentify.detection.types import COORDINATE_CONVENTION
from astroidentify.exceptions import OutputError
from astroidentify.objects.naming import OTHER_CATALOGUES
from astroidentify.objects.overlay import OverlayReport, render_object_overlay
from astroidentify.objects.types import CatalogObject, IdentificationResult, ObjectAssociation
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    write_json,
    write_text,
)
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
QUERY_JSON = "object_query.json"
RAW_RESPONSE = "simbad_response.vot"
OBJECTS_CSV = "catalog_objects.csv"
OBJECTS_JSON = "catalog_objects.json"
ASSOCIATIONS_CSV = "object_associations.csv"
SUMMARY_JSON = "identification_summary.json"
OVERLAY_PNG = "object_overlay.png"

STATEMENT = (
    "Each retained object is a catalogue entry that the WCS places in this image "
    "(catalogued_in_field). Image association evidence is reported separately as raw "
    "measurements. No confidence or probability is implied."
)

OBJECT_COLUMNS = (
    "catalogue", "catalogue_id", "main_id", "display_name", "common_names", "object_type",
    "object_type_description", "category", "candidate_type", "ra_deg", "dec_deg",
    "projected_x", "projected_y", "field_status", "centre_in_image",
    "footprint_intersects_image", "footprint_fraction_in_image", "major_axis_arcmin",
    "minor_axis_arcmin", "position_angle_deg", "extent_shape", "extent_quality", "mag_b",
    "mag_v", "redshift", "morphological_type", "n_references", "status", "exclusion_reason",
    "association", "aliases",
)  # fmt: skip
ASSOCIATION_COLUMNS = (
    "catalogue_id", "display_name", "main_id", "category",
    *(f.name for f in fields(ObjectAssociation)),
)  # fmt: skip


@dataclass(frozen=True)
class ObjectOutputPaths:
    """Artifacts written by :func:`save_object_outputs`."""

    directory: Path
    query: Path
    raw_response: Path
    objects_csv: Path
    objects_json: Path
    associations: Path
    summary: Path
    overlay: Path


def save_object_outputs(
    result: IdentificationResult,
    image: AstronomyImage,
    output_dir: str | Path,
    config: ObjectConfig,
    provenance: dict[str, Any] | None = None,
) -> tuple[ObjectOutputPaths, OverlayReport]:
    """Write all identification artifacts."""
    directory = prepare_output_dir(output_dir)
    paths = ObjectOutputPaths(
        directory=directory,
        query=directory / QUERY_JSON,
        raw_response=directory / RAW_RESPONSE,
        objects_csv=directory / OBJECTS_CSV,
        objects_json=directory / OBJECTS_JSON,
        associations=directory / ASSOCIATIONS_CSV,
        summary=directory / SUMMARY_JSON,
        overlay=directory / OVERLAY_PNG,
    )
    ensure_not_source([getattr(paths, f.name) for f in fields(paths)][1:], image.source_path)

    write_json(paths.query, query_document(result))
    try:
        paths.raw_response.write_bytes(result.query.raw_response)
    except OSError as exc:
        raise OutputError(f"could not write {paths.raw_response}: {exc}") from exc
    write_text(paths.objects_csv, objects_csv(result.objects))
    write_json(paths.objects_json, objects_document(result))
    write_text(paths.associations, associations_csv(result.retained))
    overlay, report = render_object_overlay(
        image, result, config.overlay_max_objects, config.overlay_max_labels
    )
    try:
        overlay.save(paths.overlay, format="PNG", compress_level=1)
    except OSError as exc:
        raise OutputError(f"could not write {paths.overlay}: {exc}") from exc
    write_json(paths.summary, summary_document(result, config, provenance or {}, paths, report))
    logger.info("Wrote object identification outputs to %s", directory)
    return paths, report


def query_document(result: IdentificationResult) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        **result.query.to_dict(),
        "raw_response": RAW_RESPONSE,
    }


def _object_row(obj: CatalogObject) -> dict[str, Any]:
    e = obj.extent
    return {
        **{name: getattr(obj, name) for name in OBJECT_COLUMNS if hasattr(obj, name)},
        "common_names": ";".join(obj.common_names),
        "aliases": ";".join(obj.aliases),
        "major_axis_arcmin": e.major_arcmin,
        "minor_axis_arcmin": e.minor_arcmin,
        "position_angle_deg": e.position_angle_deg,
        "extent_shape": e.shape,
        "extent_quality": e.quality,
        "mag_b": obj.magnitudes.get("B"),
        "mag_v": obj.magnitudes.get("V"),
        "association": obj.association.kind if obj.association else "",
    }


def objects_csv(objects: tuple[CatalogObject, ...]) -> str:
    """Every catalogue row (listing order)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(OBJECT_COLUMNS)
    for obj in objects:
        row = _object_row(obj)
        writer.writerow([_cell(row[c]) for c in OBJECT_COLUMNS])
    return buffer.getvalue()


def associations_csv(objects: tuple[CatalogObject, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ASSOCIATION_COLUMNS)
    for obj in objects:
        record = obj.association.to_dict() if obj.association else {}
        base = {"catalogue_id": obj.catalogue_id, "display_name": obj.display_name,
                "main_id": obj.main_id, "category": obj.category}  # fmt: skip
        writer.writerow([_cell({**base, **record}.get(c)) for c in ASSOCIATION_COLUMNS])
    return buffer.getvalue()


def objects_document(result: IdentificationResult) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "coordinate_convention": COORDINATE_CONVENTION,
        "statement": STATEMENT,
        "objects": [
            o.to_dict(include_footprint=o.status != "outside_field") for o in result.objects
        ],
    }


def summary_document(
    result: IdentificationResult,
    config: ObjectConfig,
    provenance: dict[str, Any],
    paths: ObjectOutputPaths | None = None,
    overlay: OverlayReport | None = None,
) -> dict[str, Any]:
    query = result.query
    in_field = result.in_field
    retained = result.retained
    return {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "statement": STATEMENT,
        "inputs": provenance,
        "wcs": result.wcs.to_dict() if result.wcs else None,
        "image": {
            "width": result.image_width,
            "height": result.image_height,
            "pixel_scale_arcsec": result.pixel_scale_arcsec,
        },
        "coordinate_convention": COORDINATE_CONVENTION,
        "query": {
            "service": query.service,
            "origin": query.origin,
            "queried_at": query.queried_at,
            "query_seconds": query.query_seconds,
            "cache_key": query.cache_key,
            "cache_path": query.cache_path,
            "cone": query.region.to_dict(),
            "outer_radius_deg": query.outer_radius_deg,
        },
        "counts": {
            "rows_returned": len(query.rows),
            "in_field": len(in_field),
            "in_field_by_category": dict(sorted(Counter(o.category for o in in_field).items())),
            "in_field_by_type": dict(sorted(Counter(o.object_type for o in in_field).items())),
            "retained": len(retained),
            "retained_by_category": dict(sorted(Counter(o.category for o in retained).items())),
            "annotated_on_overlay": len(overlay.drawn) if overlay else None,
            "labelled_on_overlay": len(overlay.labelled) if overlay else None,
            "detections_considered": result.n_detections,
        },
        "retained_objects": [_retained_summary(o) for o in retained],
        "primary_object": None,
        "primary_object_note": "no primary-object selection in Milestone 5; retained objects "
        "are listed in a deterministic presentation order (see policy.listing_order)",
        "policy": {
            "included_categories": list(result.included_categories),
            "include_candidate_types": config.include_candidates,
            "type_source": "SIMBAD otypedef hierarchy with explicit overrides (objects.filtering)",
            "naming": "Messier > NGC > IC > "
            + " > ".join(label for label, _ in OTHER_CATALOGUES)
            + " > SIMBAD main_id; NAME identifiers reported as common_names",
            "extent": "SIMBAD galdim (arcmin, PA east of north): ellipse if axes and angle "
            "are known, circle of the major axis if only that is, else none",
            "association": f"compact objects (no size or major axis <= "
            f'{config.compact_max_diameter_arcsec:g}"): nearest accepted detection within '
            f'{config.association_radius_arcsec:g}"; extended objects: detections in '
            "footprint and footprint-vs-annulus brightness contrast",
            "listing_order": "retained first; designation rank; larger size; more references",
        },
        "runtime_seconds": result.runtime_seconds,
        "warnings": list(result.warnings),
        "config": config.to_dict(),
        "artifacts": _artifacts(paths),
    }


def _artifacts(paths: ObjectOutputPaths | None) -> dict[str, str]:
    if paths is None:
        return {}
    return {f.name: getattr(paths, f.name).name for f in fields(paths) if f.name != "directory"}


def _retained_summary(obj: CatalogObject) -> dict[str, Any]:
    a = obj.association
    return {
        "display_name": obj.display_name,
        "main_id": obj.main_id,
        "catalogue_id": obj.catalogue_id,
        "common_names": list(obj.common_names),
        "object_type": obj.object_type,
        "object_type_description": obj.object_type_description,
        "category": obj.category,
        "ra_deg": obj.ra_deg,
        "dec_deg": obj.dec_deg,
        "projected_x": obj.projected_x,
        "projected_y": obj.projected_y,
        "field_status": obj.field_status,
        "footprint_fraction_in_image": obj.footprint_fraction_in_image,
        "extent": obj.extent.to_dict(),
        "magnitudes": obj.magnitudes,
        "association": a.to_dict() if a else None,
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return "" if value != value else format(value, ".10g")
    return str(value)

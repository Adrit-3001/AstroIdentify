"""Milestone 6.1: inspect one already-identified object (display only).

Saved Milestone 5 object table + its WCS + the original image (+ Milestone 4 Gaia rows) ->
a stretched full-frame view and a zoom with the object's catalogue centre, its catalogued
extent where reliable, bright reference stars, a scale bar and N/E arrows.

Nothing here feeds any scientific stage. The stretched pixels are written only to the
inspection PNGs, and every input artifact is only read. The object name is a
post-identification selector over saved rows; no catalogue is queried.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify import __version__
from astroidentify.astrometry.wcs import load_wcs, sky_to_pixel
from astroidentify.exceptions import InputMismatchError, OutputError
from astroidentify.inspection.references import (
    ReferenceStar,
    load_gaia,
    select_reference_stars,
)
from astroidentify.inspection.render import View, object_label, render_view
from astroidentify.inspection.selection import Selection, select_object
from astroidentify.inspection.stretch import StretchParameters, stretch
from astroidentify.objects.extent import sky_outline
from astroidentify.objects.types import ObjectExtent
from astroidentify.preprocessing.loader import load_image
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    sha256_file,
    write_json,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FULL_PNG = "inspection_full.png"
ZOOM_PNG = "inspection_zoom.png"
SUMMARY_JSON = "inspection_summary.json"
OUTLINE_POINTS = 360


@dataclass(frozen=True)
class InspectionOptions:
    stretch: StretchParameters = field(default_factory=StretchParameters)
    reference_stars: int = 15
    label_reference_stars: bool = False
    context_fraction: float = 1 / 3  # contextual crop side, as a fraction of min(W, H)
    extent_margin: float = 1.5  # crop around a known extent: this many times its half-size
    zoom_target_px: int = 1280  # enlarge the crop by an integer factor up to about this size
    zoom_magnitude_range: float = 3.0  # zoom landmarks: at most this much fainter than full
    max_zoom: int = 8


@dataclass(frozen=True)
class ExtentGeometry:
    available: bool
    shape: str
    major_arcmin: float | None
    minor_arcmin: float | None
    position_angle_deg: float | None
    quality: str | None
    visible_fraction: float | None
    extends_beyond_frame: bool | None
    drawn: str  # "closed outline", "visible arcs only", "not drawn (...)"
    outline: list[tuple[float, float]] | None


@dataclass(frozen=True, eq=False)
class InspectionResult:
    selection: Selection
    extent: ExtentGeometry
    crop: tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive), canonical pixels
    crop_reason: str
    zoom: int
    stars_full: list[ReferenceStar]
    stars_zoom: list[ReferenceStar]
    stretch_record: dict[str, Any]
    summary: dict[str, Any]
    paths: dict[str, Path]


def inspect_object(
    image_path: str | Path,
    objects_dir: str | Path,
    object_name: str,
    output_dir: str | Path,
    *,
    catalog_dir: str | Path | None = None,
    options: InspectionOptions | None = None,
) -> InspectionResult:
    """Render the inspection views for one identified object (see module docstring).

    Raises:
        ObjectSelectionError: The name matches no retained object, or several.
        InputMismatchError: The saved products belong to another image/WCS.
        OutputError: Missing inputs or unwritable outputs.
    """
    options = options or InspectionOptions()
    objects_dir = Path(objects_dir)
    summary = _read_json(objects_dir / "identification_summary.json")
    table = _read_json(objects_dir / "catalog_objects.json")["objects"]
    image = load_image(image_path)
    image_sha = sha256_file(image.source_path)
    if (summary.get("inputs") or {}).get("image_sha256") not in (None, image_sha):
        raise InputMismatchError("the object identification was made from a different image")
    wcs_info = summary.get("wcs") or {}
    wcs_path = Path(wcs_info.get("path", ""))
    if not wcs_path.is_file():
        raise OutputError(f"the WCS used by the object identification is missing: {wcs_path}")
    if wcs_info.get("sha256") not in (None, sha256_file(wcs_path)):
        raise InputMismatchError(f"{wcs_path} changed since the object identification")
    wcs, _ = load_wcs(wcs_path)
    width, height = image.width, image.height
    scale = float(summary["image"]["pixel_scale_arcsec"])

    selection = select_object(table, object_name)
    obj = selection.record
    warnings: list[str] = []
    if selection.excluded_from_overlay:
        warnings.append(
            f"{obj['display_name']} was excluded from the normal Milestone 5 overlay "
            f"({obj.get('exclusion_reason')}); it is shown here only because it was explicitly "
            "requested"
        )
    if selection.lower_tier_matches:
        warnings.append(
            f"selected by {selection.method}; also matching at a lower-priority tier: "
            + ", ".join(selection.lower_tier_matches)
        )
    px, py = sky_to_pixel(wcs, np.array([obj["ra_deg"]]), np.array([obj["dec_deg"]]))
    consistency = math.hypot(float(px[0]) - obj["projected_x"], float(py[0]) - obj["projected_y"])
    if consistency > 0.01:
        warnings.append(f"re-projected centre differs from the saved one by {consistency:.3f} px")

    if not obj.get("centre_in_image"):
        warnings.append(
            "the catalogue centre lies outside the image; the views show the nearest image region"
        )
    extent = extent_geometry(obj, wcs, width, height)
    if not extent.available:
        warnings.append(
            "no reliable catalogued angular extent: only the catalogue centre is shown; no visual "
            "boundary is claimed"
        )
    elif extent.extends_beyond_frame:
        warnings.append("the catalogued extent extends beyond the image")
    crop, crop_reason = choose_crop(obj, extent, width, height, options)
    x0, y0, x1, y1 = crop
    zoom = max(1, min(options.max_zoom, options.zoom_target_px // max(x1 - x0, y1 - y0)))

    nominal = (image.metadata.get("raster") or {}).get("nominal_max")
    pixels, stretch_record = stretch(image.data, options.stretch, nominal)
    stretch_record["display_only"] = True

    stars_full: list[ReferenceStar] = []
    stars_zoom: list[ReferenceStar] = []
    catalog = (
        Path(catalog_dir)
        if catalog_dir
        else _parent((summary.get("inputs") or {}).get("catalog_summary"))
    )
    gaia_path = catalog / "gaia_sources.csv" if catalog else None
    if options.reference_stars > 0:
        if gaia_path is not None and gaia_path.is_file():
            gaia = load_gaia(gaia_path, wcs, width, height)
            simbad_stars = [o for o in table if o.get("category") == "star"]
            full_region = (-0.5, -0.5, width - 0.5, height - 0.5)
            stars_full = select_reference_stars(
                gaia, options.reference_stars, full_region, simbad_stars, scale
            )
            faintest = max((s.g_mag for s in stars_full), default=None)
            stars_zoom = select_reference_stars(
                gaia,
                options.reference_stars,
                (x0 - 0.5, y0 - 0.5, x1 - 0.5, y1 - 0.5),
                simbad_stars,
                scale,
                faintest_g=None if faintest is None else faintest + options.zoom_magnitude_range,
            )
        else:
            warnings.append("no Milestone 4 gaia_sources.csv: reference stars not shown")

    output = prepare_output_dir(output_dir)
    inputs_dirs = {objects_dir.resolve(), *([catalog.resolve()] if catalog else [])}
    if output.resolve() in inputs_dirs:
        raise OutputError("the inspection output directory must differ from the input directories")
    paths = {"full": output / FULL_PNG, "zoom": output / ZOOM_PNG, "summary": output / SUMMARY_JSON}
    ensure_not_source(list(paths.values()), image.source_path)

    extent_note = _extent_note(extent)
    excluded_note = (
        "Excluded from the normal object overlay (type policy); shown on explicit request"
        if selection.excluded_from_overlay
        else ""
    )
    common = dict(
        wcs=wcs,
        obj=obj,
        outline=extent.outline,
        label_stars=options.label_reference_stars,
        pixel_scale_arcsec=scale,
        requested_name=selection.matched_text,
    )
    full = render_view(
        pixels,
        View(0, 0, 1),
        stars=stars_full,
        notes=[
            f"Inspection view (display-only {options.stretch.mode} stretch)",
            extent_note,
            f"Cyan: {len(stars_full)} brightest Gaia stars (landmarks, not evidence)",
            excluded_note,
        ],
        **common,
    )
    zoom_image = render_view(
        pixels[y0:y1, x0:x1],
        View(x0, y0, zoom),
        stars=stars_zoom,
        notes=[
            f"Zoom x{zoom}: x {x0}-{x1 - 1}, y {y0}-{y1 - 1}",
            crop_reason,
            extent_note,
            excluded_note,
        ],
        **common,
    )
    for key, picture in (("full", full), ("zoom", zoom_image)):
        try:
            picture.save(paths[key], format="PNG", compress_level=1)
        except OSError as exc:
            raise OutputError(f"could not write {paths[key]}: {exc}") from exc

    document = {
        "schema_version": SCHEMA_VERSION,
        "software": {"name": "astroidentify", "version": __version__},
        "statement": "Display-only inspection of an object already identified by Milestone 5. "
        "The stretch, markers and reference stars are visual aids: no scientific result is "
        "recomputed or changed, and the object name was used only to select a saved row.",
        "input_image": str(image.source_path),
        "input_image_sha256": image_sha,
        "image": {"width": width, "height": height, "pixel_scale_arcsec": scale},
        "objects_artifacts": str(objects_dir),
        "selection": {
            "query": object_name,
            "method": selection.method,
            "matched_text": selection.matched_text,
            "lower_tier_matches": list(selection.lower_tier_matches),
            "milestone5_status": obj.get("status"),
            "milestone5_exclusion_reason": obj.get("exclusion_reason"),
            "excluded_from_normal_overlay": selection.excluded_from_overlay,
            "explicitly_selected_for_inspection": True,
            "note": (
                "excluded from the normal Milestone 5 overlay by its type policy, but explicitly "
                "selected for this inspection; Milestone 5 filtering is unchanged"
                if selection.excluded_from_overlay
                else "retained (identified) by Milestone 5"
            ),
        },
        "label": {
            **object_label(obj, selection.matched_text),
            "note": "overlay presentation only; catalogue identity and type are unchanged",
        },
        "object": {
            "catalogue": obj.get("catalogue"),
            "catalogue_id": obj["catalogue_id"],
            "display_name": obj["display_name"],
            "main_id": obj.get("main_id"),
            "object_type": obj.get("object_type"),
            "object_type_description": obj.get("object_type_description"),
            "category": obj.get("category"),
            "aliases": obj.get("aliases"),
            "common_names": obj.get("common_names"),
            "ra_deg": obj["ra_deg"],
            "dec_deg": obj["dec_deg"],
            "projected_x": obj["projected_x"],
            "projected_y": obj["projected_y"],
            "reprojection_difference_px": consistency,
            "field_status": obj.get("field_status"),
        },
        "wcs": {**wcs_info, "pixel_origin": "canonical 0-based (Astropy origin=0); no +1 offset"},
        "extent": {k: v for k, v in asdict(extent).items() if k != "outline"}
        | {
            "extent_available": extent.available,
            "outline_points_in_image": None
            if extent.outline is None
            else sum(
                -0.5 <= x <= width - 0.5 and -0.5 <= y <= height - 0.5 for x, y in extent.outline
            ),
        },
        "stretch": stretch_record,
        "reference_stars": {
            "policy": "brightest Gaia DR3 stars (G) from Milestone 4 gaia_sources.csv, "
            "re-projected through the same WCS; names only from saved SIMBAD star rows",
            "requested": options.reference_stars,
            "zoom_magnitude_range": options.zoom_magnitude_range,
            "labels": "names and G magnitudes" if options.label_reference_stars else "names only",
            "full": [s.to_dict() for s in stars_full],
            "zoom": [s.to_dict() for s in stars_zoom],
        },
        "crop": {
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "reason": crop_reason,
            "zoom_factor": zoom,
            "mapping": "view = (canonical - origin) * zoom + (zoom - 1) / 2",
        },
        "warnings": warnings,
        "artifacts": {k: str(v) for k, v in paths.items()},
    }
    write_json(paths["summary"], document)
    logger.info("Wrote inspection of %s to %s", obj["display_name"], output)
    return InspectionResult(
        selection,
        extent,
        crop,
        crop_reason,
        zoom,
        stars_full,
        stars_zoom,
        stretch_record,
        document,
        paths,
    )


def extent_geometry(obj: dict, wcs, width: int, height: int) -> ExtentGeometry:
    """Projected catalogue extent of the selected object (nothing invented when absent)."""
    e = obj.get("extent") or {}
    shape = e.get("shape", "none")
    base = dict(
        shape=shape,
        major_arcmin=e.get("major_arcmin"),
        minor_arcmin=e.get("minor_arcmin"),
        position_angle_deg=e.get("position_angle_deg"),
        quality=e.get("quality"),
    )
    if shape == "none":
        return ExtentGeometry(
            available=False,
            **base,
            visible_fraction=None,
            extends_beyond_frame=None,
            drawn="not drawn (no catalogued size)",
            outline=None,
        )
    extent = ObjectExtent(
        **{
            k: e.get(k)
            for k in ("major_arcmin", "minor_arcmin", "position_angle_deg", "quality", "shape")
        }
    )
    ra, dec = sky_outline(obj["ra_deg"], obj["dec_deg"], extent, n=OUTLINE_POINTS)
    xs, ys = sky_to_pixel(wcs, ra, dec, best_effort=True)
    outline = [
        (float(x), float(y))
        for x, y in zip(xs, ys, strict=True)
        if np.isfinite(x) and np.isfinite(y)
    ]
    inside = [(-0.5 <= x <= width - 0.5 and -0.5 <= y <= height - 0.5) for x, y in outline]
    fraction = obj.get("footprint_fraction_in_image")
    beyond = (not all(inside)) or len(outline) < OUTLINE_POINTS
    if not any(inside):
        drawn = "not drawn (the outline lies entirely outside the image)"
        outline = None
    elif beyond:
        drawn = "visible arcs only (the outline is clipped at the image edges)"
    else:
        drawn = "closed outline"
    return ExtentGeometry(
        available=True,
        **base,
        visible_fraction=fraction,
        extends_beyond_frame=beyond,
        drawn=drawn,
        outline=outline,
    )


def choose_crop(
    obj, extent: ExtentGeometry, width, height, options
) -> tuple[tuple[int, int, int, int], str]:
    """Deterministic zoom crop: around a known, mostly visible extent; otherwise contextual."""
    cx, cy = obj["projected_x"], obj["projected_y"]
    context = max(32, round(min(width, height) * options.context_fraction))
    if extent.available and extent.outline and not extent.extends_beyond_frame:
        xs = [p[0] for p in extent.outline]
        ys = [p[1] for p in extent.outline]
        half = options.extent_margin * max(max(xs) - min(xs), max(ys) - min(ys)) / 2
        side = max(round(2 * half), 64)
        reason = f"crop covers the catalogued extent with {options.extent_margin:g}x margin"
    else:
        side = context
        reason = "contextual crop around the catalogue centre; it is NOT a measured object boundary"
    side = min(side, width, height)
    x0 = int(min(max(round(cx - side / 2), 0), width - side))
    y0 = int(min(max(round(cy - side / 2), 0), height - side))
    return (x0, y0, x0 + side, y0 + side), reason


def _extent_note(extent: ExtentGeometry) -> str:
    if not extent.available:
        return "Catalogue centre only: no reliable catalogued extent (no boundary drawn)"
    kind = "ellipse" if extent.shape == "ellipse" else "circle of the major axis (no PA)"
    size = f"{extent.major_arcmin:.3g}'" + (
        f" x {extent.minor_arcmin:.3g}'" if extent.minor_arcmin else ""
    )
    return f"Catalogued extent {size} ({kind}); {extent.drawn}"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OutputError(f"could not read {path}: {exc}") from exc


def _parent(path: str | None) -> Path | None:
    return Path(path).parent if path else None

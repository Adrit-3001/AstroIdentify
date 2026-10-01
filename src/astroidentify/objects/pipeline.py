"""Milestone 5 pipeline: WCS -> sky region -> SIMBAD -> projection -> filtering ->
association -> listing.

Typical use::

    inputs = load_identify_inputs(image, astrometry_dir="outputs/x-astrometry",
                                  catalog_dir="outputs/x-catalog")
    result = identify_objects(inputs.wcs, inputs.width, inputs.height,
                              SimbadProvider(config), config, inputs.detections,
                              inputs.plane, inputs.wcs_choice)

The only path to an identification is: WCS -> WCS-derived sky region -> catalogue query ->
pixel projection -> filtering/association. No object name, target coordinate or filename
is ever an input.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from astroidentify.astrometry.wcs import load_wcs
from astroidentify.catalogs.footprint import project_to_image, query_region, validate_wcs
from astroidentify.catalogs.types import QueryRegion
from astroidentify.config import ObjectConfig
from astroidentify.detection.outputs import load_sources
from astroidentify.detection.types import Source
from astroidentify.exceptions import (
    ConfigurationError,
    InputMismatchError,
    InvalidPlateSolutionError,
    InvalidWCSError,
)
from astroidentify.objects.association import (
    associate_point_source,
    extended_evidence,
    is_compact,
)
from astroidentify.objects.extent import image_overlap, parse_extent
from astroidentify.objects.filtering import (
    ALL_CATEGORIES,
    categorise,
    exclusion_reason,
    is_candidate_type,
)
from astroidentify.objects.naming import (
    common_names,
    display_name,
    normalise_identifier,
    split_identifiers,
)
from astroidentify.objects.types import (
    FIELD_CENTRE_IN_IMAGE,
    FIELD_EXTENT_OVERLAPS,
    FIELD_NOT_PROJECTABLE,
    FIELD_OUTSIDE,
    STATUS_IN_FIELD,
    STATUS_OUTSIDE,
    STATUS_TYPE_EXCLUDED,
    CatalogObject,
    IdentificationResult,
    ObjectQueryResult,
    WcsChoice,
)
from astroidentify.preprocessing.loader import load_image
from astroidentify.serialization import sha256_file
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)

PLATE_SOLUTION_JSON = "plate_solution.json"
SOLUTION_WCS = "solution.wcs"
REFINED_WCS = "refined_solution.wcs"
CATALOG_SUMMARY_JSON = "catalog_match_summary.json"

_STATUS_ORDER = {STATUS_IN_FIELD: 0, STATUS_TYPE_EXCLUDED: 1, STATUS_OUTSIDE: 2}


class ObjectProvider(Protocol):
    """Anything that returns named-object rows for a query region (e.g. SimbadProvider)."""

    def query(self, region: QueryRegion) -> ObjectQueryResult: ...


@dataclass(frozen=True, eq=False)
class IdentifyInputs:
    """Saved products for one image, checked for consistency."""

    image: AstronomyImage
    wcs: WCS
    wcs_choice: WcsChoice
    detections: list[Source] | None
    plane: np.ndarray
    provenance: dict[str, Any]

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


# --------------------------------------------------------------------------- inputs


def select_wcs(
    astrometry_dir: Path | None, catalog_dir: Path | None, explicit: Path | None
) -> tuple[Path, WcsChoice]:
    """Pick the WCS: explicit file > catalogue-refined WCS > plate-solver WCS.

    Raises:
        InvalidPlateSolutionError: No usable WCS file.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise InvalidPlateSolutionError(f"WCS file not found: {explicit}")
        return explicit, WcsChoice(str(explicit), None, "explicit", sha256_file(explicit))
    if catalog_dir is not None and (catalog_dir / REFINED_WCS).is_file():
        path = catalog_dir / REFINED_WCS
        return path, WcsChoice(str(path), True, "catalog_refined", sha256_file(path))
    if astrometry_dir is not None and (astrometry_dir / SOLUTION_WCS).is_file():
        path = astrometry_dir / SOLUTION_WCS
        return path, WcsChoice(str(path), False, "plate_solution", sha256_file(path))
    looked = [str(d) for d in (catalog_dir, astrometry_dir) if d is not None]
    raise InvalidPlateSolutionError(
        f"no WCS found (looked for {REFINED_WCS} / {SOLUTION_WCS} in {looked or 'nothing'}); "
        "pass --astrometry, --catalog or --wcs"
    )


def load_identify_inputs(
    image_path: str | Path,
    *,
    astrometry_dir: str | Path | None = None,
    catalog_dir: str | Path | None = None,
    wcs_path: str | Path | None = None,
    detections_path: str | Path | None = None,
) -> IdentifyInputs:
    """Load the image, choose and validate the WCS, and load detections if available.

    Detections default to the catalogue run's recorded ``sources.json``. Every saved
    product that records an image hash must match this image.

    Raises:
        InvalidPlateSolutionError: Unsolved plate solution or missing/invalid WCS.
        InputMismatchError: A product was made from a different image or image size.
    """
    image = load_image(image_path)
    image_sha = sha256_file(image.source_path)
    astrometry = Path(astrometry_dir) if astrometry_dir is not None else None
    catalog = Path(catalog_dir) if catalog_dir is not None else None
    provenance: dict[str, Any] = {"image": str(image.source_path), "image_sha256": image_sha}

    if astrometry is not None:
        plate = _read_json(astrometry / PLATE_SOLUTION_JSON, InvalidPlateSolutionError)
        if not plate.get("solved"):
            raise InvalidPlateSolutionError(
                f"plate solution in {astrometry} is not solved (status {plate.get('status')!r})"
            )
        if (plate.get("input") or {}).get("source_sha256") not in (None, image_sha):
            raise InputMismatchError("the plate solution was made from a different image")
        provenance["plate_solution"] = str(astrometry / PLATE_SOLUTION_JSON)
        provenance["plate_solution_mode"] = plate.get("mode")
        provenance["plate_solution_constraints"] = plate.get("constraints")
    catalog_inputs: dict[str, Any] = {}
    if catalog is not None:
        summary = _read_json(catalog / CATALOG_SUMMARY_JSON, InvalidPlateSolutionError)
        catalog_inputs = summary.get("inputs") or {}
        if catalog_inputs.get("image_sha256") not in (None, image_sha):
            raise InputMismatchError("the catalogue-match run was made from a different image")
        provenance["catalog_summary"] = str(catalog / CATALOG_SUMMARY_JSON)

    path, choice = select_wcs(astrometry, catalog, Path(wcs_path) if wcs_path else None)
    try:
        wcs, header = load_wcs(path)
    except InvalidWCSError as exc:
        raise InvalidPlateSolutionError(str(exc)) from exc
    if (header.get("IMAGEW"), header.get("IMAGEH")) not in (
        (None, None),
        (image.width, image.height),
    ):
        raise InputMismatchError(
            f"the WCS was made for {header.get('IMAGEW')} x {header.get('IMAGEH')} pixels, the "
            f"image is {image.width} x {image.height}"
        )
    provenance["wcs"] = choice.to_dict()

    detections = None
    source_path = detections_path or catalog_inputs.get("detections")
    if source_path is not None:
        source_path = Path(source_path)
        meta = source_path.with_name("detection_metadata.json")
        recorded = (
            _read_json(meta, InputMismatchError).get("source_sha256") if meta.is_file() else None
        )
        if recorded not in (None, image_sha):
            raise InputMismatchError("the detection catalogue was made from a different image")
        detections = [s for s in load_sources(source_path) if s.accepted]
        provenance["detections"] = str(source_path)
    plane = np.asarray(image.data, np.float32)
    if plane.ndim == 3:
        plane = plane.mean(axis=2)  # the Milestone 2 detection-plane definition
    return IdentifyInputs(image, wcs, choice, detections, plane, provenance)


def _read_json(path: Path, error: type[Exception]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise error(f"could not read {path}: {exc}") from exc


# --------------------------------------------------------------------------- identification


def identify_objects(
    wcs: WCS,
    width: int,
    height: int,
    provider: ObjectProvider,
    config: ObjectConfig | None = None,
    detections: list[Source] | None = None,
    plane: np.ndarray | None = None,
    wcs_choice: WcsChoice | None = None,
) -> IdentificationResult:
    """Query the object catalogue for the WCS footprint and place every row on the image.

    Raises:
        InvalidPlateSolutionError: The WCS fails validation.
        ConfigurationError: Unknown category in ``config.included_categories``.
        CatalogQueryError (and subclasses): Provider failures (never an empty success).
    """
    config = config or ObjectConfig()
    unknown = set(config.included_categories) - set(ALL_CATEGORIES)
    if unknown:
        raise ConfigurationError(f"unknown object categories {sorted(unknown)}")
    started = time.monotonic()
    geometry = validate_wcs(wcs, width, height)
    region = query_region(wcs, width, height, config.query_margin_arcsec)
    query = provider.query(region)
    objects = build_objects(query.rows, wcs, width, height, region, config)
    objects = [
        _with_association(o, config, detections, plane, geometry.pixel_scale_arcsec)
        if o.retained
        else o
        for o in objects
    ]
    objects.sort(key=listing_key)
    warnings = list(query.warnings)
    if not query.rows:
        warnings.append("the catalogue returned no objects for this field")
    elif not any(o.retained for o in objects):
        warnings.append("no catalogued object of an included type lies in the field")
    if detections is None:
        warnings.append("no detection catalogue: point-source association not attempted")
    return IdentificationResult(
        query=query,
        objects=tuple(objects),
        wcs=wcs_choice,
        image_width=width,
        image_height=height,
        pixel_scale_arcsec=geometry.pixel_scale_arcsec,
        n_detections=None if detections is None else len(detections),
        included_categories=tuple(config.included_categories),
        runtime_seconds=time.monotonic() - started,
        warnings=tuple(warnings),
    )


def build_objects(
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    wcs: WCS,
    width: int,
    height: int,
    region: QueryRegion,
    config: ObjectConfig,
) -> list[CatalogObject]:
    """Project, name and classify every catalogue row (no association yet)."""
    if not rows:
        return []
    ra = np.array([r["ra"] for r in rows], float)
    dec = np.array([r["dec"] for r in rows], float)
    # Canonical pixels via Astropy origin=0; the Astrometry.net +1 never applies here.
    x, y, centre_in = project_to_image(wcs, ra, dec, width, height)
    centre = SkyCoord(region.centre.ra_deg, region.centre.dec_deg, unit="deg")
    distance_deg = centre.separation(SkyCoord(ra, dec, unit="deg")).deg
    image_radius_deg = max(region.corner_distances_deg.values())

    objects = []
    for k, row in enumerate(rows):
        identifiers = split_identifiers(row.get("ids"))
        main_id = normalise_identifier(row["main_id"] or str(row["oid"]))
        name, rank = display_name(main_id, identifiers)
        extent = parse_extent(row.get("galdim_majaxis"), row.get("galdim_minaxis"),
                              row.get("galdim_angle"), row.get("galdim_qual"))  # fmt: skip
        inside = bool(centre_in[k])
        intersects, fraction, outline = inside, (1.0 if inside else None), None
        # The outline is only worth computing if the ellipse can reach the image at all.
        if extent.has_size and distance_deg[k] <= image_radius_deg + extent.semi_major_deg:
            intersects, fraction, outline = image_overlap(
                wcs, ra[k], dec[k], extent, width, height, inside
            )
        if inside:
            field = FIELD_CENTRE_IN_IMAGE
        elif intersects:
            field = FIELD_EXTENT_OVERLAPS
        elif not (np.isfinite(x[k]) and np.isfinite(y[k])):
            field = FIELD_NOT_PROJECTABLE
        else:
            field = FIELD_OUTSIDE
        category = categorise(row.get("otype"), row.get("otype_path"))
        candidate = is_candidate_type(row.get("otype"))
        if field in (FIELD_CENTRE_IN_IMAGE, FIELD_EXTENT_OVERLAPS):
            reason = exclusion_reason(
                category, candidate, config.included_categories, config.include_candidates
            )
            status = STATUS_IN_FIELD if reason is None else STATUS_TYPE_EXCLUDED
        else:
            reason, status = "outside the image footprint", STATUS_OUTSIDE
        magnitudes = {
            band: float(row[column])
            for band, column in (("B", "mag_b"), ("V", "mag_v"))
            if row.get(column) is not None
        }
        objects.append(
            CatalogObject(
                catalogue="SIMBAD",
                catalogue_id=int(row["oid"]),
                main_id=main_id,
                display_name=name,
                common_names=common_names(identifiers),
                aliases=identifiers,
                object_type=row.get("otype") or "",
                object_type_description=row.get("otype_description"),
                object_type_path=row.get("otype_path"),
                category=category,
                candidate_type=candidate,
                ra_deg=float(ra[k]),
                dec_deg=float(dec[k]),
                projected_x=float(x[k]),
                projected_y=float(y[k]),
                field_status=field,
                centre_in_image=inside,
                footprint_intersects_image=bool(intersects),
                footprint_fraction_in_image=fraction,
                extent=extent,
                magnitudes=magnitudes,
                redshift=row.get("rvz_redshift"),
                morphological_type=row.get("morph_type"),
                n_references=row.get("nbref"),
                status=status,
                exclusion_reason=reason,
                footprint_px=outline if status != STATUS_OUTSIDE else None,
                name_rank=rank,
            )
        )
    return objects


def _with_association(
    obj: CatalogObject,
    config: ObjectConfig,
    detections: list[Source] | None,
    plane: np.ndarray | None,
    pixel_scale_arcsec: float,
) -> CatalogObject:
    if is_compact(obj, config.compact_max_diameter_arcsec):
        association = associate_point_source(
            obj, detections, pixel_scale_arcsec, config.association_radius_arcsec
        )
    else:
        association = extended_evidence(obj, detections, plane, config.evidence_min_pixels)
    return replace(obj, association=association)


def listing_key(obj: CatalogObject) -> tuple:
    """Deterministic listing order (a presentation order, not a verdict).

    Retained objects first; then catalogue designation rank (Messier, NGC, IC, other
    common catalogues, other), larger catalogued size, more literature references, and
    finally the identifiers themselves.
    """
    return (
        _STATUS_ORDER[obj.status],
        obj.name_rank,
        -(obj.extent.major_arcmin or 0.0),
        -(obj.n_references or 0),
        obj.display_name,
        obj.catalogue_id,
    )

"""Milestone 6 pipeline: saved Milestone 2-5 products -> per-object evidence -> support level.

Typical use::

    inputs = load_evidence_inputs(image, "outputs/x-objects")
    result = assess_evidence(inputs, EvidenceConfig())

Only retained Milestone 5 objects are assessed. Objects excluded by the Milestone 5 type
policy (for example ordinary stars) stay excluded. No network access is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from astroidentify.config import EvidenceConfig
from astroidentify.detection.types import Source
from astroidentify.evidence.ambiguity import assess_ambiguity
from astroidentify.evidence.astrometry import LocalResiduals, field_astrometry
from astroidentify.evidence.compact import assess_compact
from astroidentify.evidence.explanations import explain
from astroidentify.evidence.extended import MOSTLY_OUTSIDE, assess_extended
from astroidentify.evidence.features import EvidenceInputs
from astroidentify.evidence.scoring import aggregate
from astroidentify.evidence.types import (
    EVIDENCE_VERSION,
    PATH_COMPACT,
    PATH_EXTENDED,
    AmbiguityEvidence,
    AstrometryEvidence,
    CatalogueEvidence,
    EvidenceResult,
    ObjectEvidence,
)

logger = logging.getLogger(__name__)

PositionScale = Callable[[float, float], tuple[float | None, str | None, int | None, float | None]]

#: Codes describing data quality (as opposed to supporting or missing evidence).
DATA_QUALITY_CODES = frozenset(
    {
        "OBJECT_TRUNCATED",
        "OBJECT_LARGER_THAN_FRAME",
        "OBJECT_MOSTLY_OUTSIDE",
        "POSITION_UNCERTAIN_FOR_SIZE",
        "SOURCE_LOW_SNR",
        "SOURCE_EDGE",
        "SOURCE_SATURATED_CALIBRATED",
        "SOURCE_SATURATED_UNCALIBRATED",
        "POSITION_NOT_DISCRIMINATING",
        "CANDIDATE_TYPE",
        "CATALOGUE_WARNINGS",
        "BLIND_WCS",
        "ASTROMETRY_ADEQUATE",
        "ASTROMETRY_POOR",
        "ASTROMETRY_UNAVAILABLE",
    }
)
MISSING_CODES = {
    "MISSING_SIZE": "angular_size",
    "MISSING_POSITION_ANGLE": "position_angle",
    "MISSING_EPOCH": "observation_epoch",
    "MISSING_DETECTIONS": "detection_catalogue",
    "MISSING_POSITION_SCALE": "positional_scale",
    "ASTROMETRY_FIELD_RESIDUALS": "local_gaia_residuals",
    "EXTENDED_CONTRAST_UNAVAILABLE": "footprint_contrast",
    "STRUCTURE_NOT_MEASURED": "structure_position",
}


def assess_evidence(inputs: EvidenceInputs, config: EvidenceConfig | None = None) -> EvidenceResult:
    """Assess every retained object of a saved Milestone 5 run."""
    config = config or EvidenceConfig()
    field = field_astrometry(inputs, config)
    local = LocalResiduals(inputs, config)
    query_warnings = tuple(inputs.objects_summary.get("warnings") or ())
    gaia_map = inputs.gaia.gaia_for_detection() if inputs.gaia is not None else {}

    def scale(x: float, y: float):
        return local.scale_at(field, x, y, inputs.pixel_scale_arcsec)

    objects = assess_objects(
        inputs.objects,
        field=field,
        position_scale=scale,
        plane=inputs.plane,
        detections=inputs.detections,
        fwhm_px=inputs.fwhm_px,
        gaia_for_detection=gaia_map,
        pixel_scale=inputs.pixel_scale_arcsec,
        compact_max_arcsec=inputs.compact_max_diameter_arcsec,
        config=config,
        catalogue_warnings=bool(query_warnings),
        provenance={
            "objects": inputs.provenance.get("objects"),
            "wcs_sha256": (inputs.provenance.get("objects_wcs") or {}).get("sha256"),
        },
    )
    warnings = list(query_warnings)
    if inputs.detections is None:
        warnings.append("no detection catalogue: compact objects cannot be associated")
    if field.grade == "unavailable":
        warnings.append("no residual statistics apply to the WCS used: all objects abstain")
    return EvidenceResult(
        objects=tuple(objects),
        image_width=inputs.width,
        image_height=inputs.height,
        pixel_scale_arcsec=inputs.pixel_scale_arcsec,
        field_astrometry=field,
        config=config,
        provenance=inputs.provenance,
        warnings=tuple(warnings),
    )


def object_path(obj: dict, compact_max_arcsec: float) -> str:
    """Milestone 5's compact/extended split (no size, or a major axis up to the limit)."""
    extent = obj.get("extent") or {}
    if extent.get("shape", "none") == "none":
        return PATH_COMPACT
    return (
        PATH_COMPACT
        if (extent.get("major_arcmin") or 0) * 60 <= compact_max_arcsec
        else PATH_EXTENDED
    )


def assess_objects(
    objects: list[dict],
    *,
    field: AstrometryEvidence,
    position_scale: PositionScale,
    plane: np.ndarray,
    detections: list[Source] | None,
    fwhm_px: float | None,
    gaia_for_detection: dict[int, int],
    pixel_scale: float,
    compact_max_arcsec: float,
    config: EvidenceConfig,
    catalogue_warnings: bool = False,
    provenance: dict | None = None,
) -> list[ObjectEvidence]:
    """Core assessment on plain Milestone 5 object records (see module docstring)."""
    height, width = plane.shape
    density = len(detections) / float(width * height) if detections is not None else None
    staged = []
    for obj in objects:
        x, y = float(obj["projected_x"]), float(obj["projected_y"])
        r50, r50_source, n_local, local_radius = position_scale(x, y)
        astrometry = replace(
            field,
            r50_px=r50,
            r50_arcsec=None if r50 is None else r50 * pixel_scale,
            r50_source=r50_source,
            n_local_matches=n_local,
            local_radius_px=local_radius,
        )
        codes = _astrometry_codes(astrometry)
        catalogue, catalogue_codes = _catalogue(obj)
        codes += catalogue_codes
        if catalogue_warnings:
            codes.append("CATALOGUE_WARNINGS")
        path = object_path(obj, compact_max_arcsec)
        compact = extended = None
        if path == PATH_COMPACT:
            compact, path_codes, caps = assess_compact(
                x, y, r50, detections, density, gaia_for_detection, pixel_scale, config
            )
            image_grade = compact.grade
            radius = None
        else:
            extended, path_codes, caps = assess_extended(
                obj, plane, detections, fwhm_px, r50, pixel_scale, config
            )
            image_grade = extended.grade
            radius = extended.radius_px
        codes += path_codes
        staged.append(
            (
                obj,
                path,
                astrometry,
                catalogue,
                compact,
                extended,
                image_grade,
                caps,
                codes,
                radius,
                r50,
            )
        )

    ambiguity = assess_ambiguity(
        [
            dict(
                id=int(o["catalogue_id"]),
                name=o["display_name"],
                type=o["object_type"],
                path=p,
                x=float(o["projected_x"]),
                y=float(o["projected_y"]),
                radius_px=radius,
                r50_px=r50,
            )
            for o, p, *_, radius, r50 in staged
        ],
        config,
    )

    results = []
    for (
        obj,
        path,
        astrometry,
        catalogue,
        compact,
        extended,
        image_grade,
        caps,
        codes,
        _,
        _,
    ) in staged:
        amb = ambiguity.get(int(obj["catalogue_id"]), AmbiguityEvidence("none"))
        if amb.competitors:
            codes.append("AMBIGUOUS_CANDIDATES")
        if any(r.relation == "contains" for r in amb.related):
            codes.append("CONTAINS_SUBSTRUCTURE")
        if any(r.relation == "within" for r in amb.related):
            codes.append("WITHIN_LARGER_OBJECT")
        level, applied, extra = aggregate(
            image_grade=image_grade,
            astrometry_grade=astrometry.grade,
            candidate_type=catalogue.candidate_type,
            mostly_outside=extended is not None and extended.visibility == MOSTLY_OUTSIDE,
            ambiguity_severity=amb.severity,
            path_caps=caps,
        )
        codes += extra + (["AMBIGUITY_SEVERE"] if amb.severity == "severe" else [])
        codes = list(dict.fromkeys(codes))  # dedupe, keep order
        evidence = ObjectEvidence(
            catalogue_id=int(obj["catalogue_id"]),
            display_name=obj["display_name"],
            object_type=obj["object_type"],
            category=obj["category"],
            evidence_version=EVIDENCE_VERSION,
            path=path,
            projected_x=float(obj["projected_x"]),
            projected_y=float(obj["projected_y"]),
            astrometry=astrometry,
            catalogue=catalogue,
            compact=compact,
            extended=extended,
            ambiguity=amb,
            image_grade=image_grade,
            data_quality_flags=tuple(c for c in codes if c in DATA_QUALITY_CODES),
            missing=tuple(MISSING_CODES[c] for c in codes if c in MISSING_CODES),
            caps=tuple(dict.fromkeys(applied)),
            support_level=level,
            reason_codes=tuple(codes),
            explanation="",
            provenance={
                **(provenance or {}),
                "catalogue": obj.get("catalogue", "SIMBAD"),
                "main_id": obj.get("main_id"),
            },
        )
        results.append(replace(evidence, explanation=explain(evidence)))
    return results


def _astrometry_codes(a: AstrometryEvidence) -> list[str]:
    codes = [f"ASTROMETRY_{a.grade.upper()}"]
    if a.r50_source == "local_gaia":
        codes.append("ASTROMETRY_LOCAL_RESIDUALS")
    elif a.r50_source is not None:
        codes.append("ASTROMETRY_FIELD_RESIDUALS")
    if a.wcs_refined is False:
        codes.append("BLIND_WCS")
    if a.epoch_propagated is not True:
        codes.append("MISSING_EPOCH")
    return codes


def _catalogue(obj: dict) -> tuple[CatalogueEvidence, list[str]]:
    extent = obj.get("extent") or {}
    shape = extent.get("shape", "none")
    checks = {
        "angular_size": shape != "none",
        "position_angle": shape == "ellipse",
        "magnitude": bool(obj.get("magnitudes")),
        "redshift": obj.get("redshift") is not None,
        "morphological_type": obj.get("morphological_type") is not None,
        "aliases": len(obj.get("aliases") or ()) > 1,
    }
    codes = []
    if shape == "none":
        codes.append("MISSING_SIZE")
    elif shape == "circle":
        codes.append("MISSING_POSITION_ANGLE")
    if obj.get("candidate_type"):
        codes.append("CANDIDATE_TYPE")
    evidence = CatalogueEvidence(
        catalogue=obj.get("catalogue", "SIMBAD"),
        catalogue_id=int(obj["catalogue_id"]),
        main_id=obj.get("main_id", ""),
        display_name=obj["display_name"],
        aliases_count=len(obj.get("aliases") or ()),
        object_type=obj["object_type"],
        object_type_description=obj.get("object_type_description"),
        category=obj["category"],
        candidate_type=bool(obj.get("candidate_type")),
        has_size=shape != "none",
        has_position_angle=shape == "ellipse",
        major_axis_arcmin=extent.get("major_arcmin"),
        minor_axis_arcmin=extent.get("minor_arcmin"),
        position_angle_deg=extent.get("position_angle_deg"),
        metadata_present=tuple(k for k, v in checks.items() if v),
        metadata_missing=tuple(k for k, v in checks.items() if not v),
    )
    return evidence, codes

"""Reason-code catalogue and short human-readable explanations (generic, never per object)."""

from __future__ import annotations

from astroidentify.evidence.types import (
    LEVEL_CATALOGUE_ONLY,
    LEVEL_INSUFFICIENT,
    PATH_COMPACT,
    ObjectEvidence,
)

#: Every reason code with its meaning (also written to the evidence summary).
REASON_CODES: dict[str, str] = {
    "ASTROMETRY_PRECISE": "WCS verified by many Gaia stars with small residuals",
    "ASTROMETRY_ADEQUATE": "WCS verified by fewer stars or larger residuals (caps at moderate)",
    "ASTROMETRY_POOR": "WCS poorly verified (caps at weak)",
    "ASTROMETRY_UNAVAILABLE": "no residual statistics apply to the WCS used (abstain)",
    "ASTROMETRY_LOCAL_RESIDUALS": "positional scale from nearby Gaia residuals",
    "ASTROMETRY_FIELD_RESIDUALS": "positional scale from a field-level residual (no local value)",
    "BLIND_WCS": "objects were placed with the unrefined plate-solver WCS",
    "MISSING_EPOCH": "no observation epoch: catalogue-epoch positions (proper motion not applied)",
    "MISSING_DETECTIONS": "no detection catalogue: point-source association not possible",
    "MISSING_POSITION_SCALE": "no positional scale: separations cannot be normalized",
    "POSITION_MATCH_CLOSE": "an accepted detection lies within 1.5 r50 of the catalogue position",
    "POSITION_MATCH_CONSISTENT": "an accepted detection lies within 3 r50",
    "POSITION_MATCH_POOR": "nearest detection lies 3-5 r50 away: statistically inconsistent",
    "POSITION_NOT_DISCRIMINATING": "unrelated detections are likely inside the uncertainty region",
    "NO_IMAGE_ASSOCIATION": "no accepted detection near the catalogue position",
    "SOURCE_LOW_SNR": "associated detection has low SNR (caps at moderate)",
    "SOURCE_EDGE": "associated detection is edge-flagged (caps at moderate)",
    "SOURCE_SATURATED_CALIBRATED": "associated detection is saturated; calibrated centroid used",
    "SOURCE_SATURATED_UNCALIBRATED": "associated detection is saturated without a calibrated "
    "centroid (caps at moderate)",
    "DETECTION_GAIA_MATCHED": "associated detection also matches a Gaia source (context only)",
    "EXTENDED_CONTRAST_STRONG": "footprint is much brighter than its surroundings",
    "EXTENDED_CONTRAST_MODERATE": "footprint is clearly brighter than its surroundings",
    "EXTENDED_CONTRAST_WEAK": "footprint is slightly but significantly brighter",
    "EXTENDED_NOT_DETECTED": "footprint is not brighter than its surroundings",
    "EXTENDED_CONTRAST_UNAVAILABLE": "contrast could not be measured (too few in-image pixels)",
    "STRUCTURE_CENTRED": "visible structure is centred on the catalogue position",
    "STRUCTURE_PARTLY_OFFSET": "visible structure is partly offset (caps at moderate)",
    "STRUCTURE_OFFSET": "visible structure is offset by more than half the radius (caps at weak)",
    "STRUCTURE_NOT_MEASURED": "too few structure pixels to locate the visible structure",
    "EXTENT_MOSTLY_VISIBLE": "most of the catalogued extent is inside the image",
    "OBJECT_TRUNCATED": "only part of the catalogued extent is inside the image (caps at moderate)",
    "OBJECT_LARGER_THAN_FRAME": "the object is larger than the image (caps at moderate)",
    "OBJECT_MOSTLY_OUTSIDE": "the object is almost entirely outside the image (abstain)",
    "POSITION_UNCERTAIN_FOR_SIZE": "positional scale is large relative to the object size",
    "MISSING_SIZE": "no catalogued angular size: assessed as a compact object",
    "MISSING_POSITION_ANGLE": "no catalogued orientation: footprint drawn as a circle",
    "CANDIDATE_TYPE": "SIMBAD lists the type as a candidate (caps at moderate)",
    "CATALOGUE_WARNINGS": "the catalogue query reported warnings",
    "AMBIGUOUS_CANDIDATES": "other catalogue objects compete for the same image evidence",
    "AMBIGUITY_SEVERE": "several indistinguishable catalogue objects (abstain)",
    "CONTAINS_SUBSTRUCTURE": "smaller catalogued objects lie inside the footprint (context)",
    "WITHIN_LARGER_OBJECT": "lies inside a larger catalogued object's footprint (context)",
    "ABSTAIN_NO_ASTROMETRY": "abstained: astrometric prerequisites missing",
    "ABSTAIN_MOSTLY_OUTSIDE": "abstained: object almost entirely off-frame",
    "ABSTAIN_AMBIGUOUS": "abstained: severe ambiguity",
    "ABSTAIN_NO_SUPPORT": "abstained: no image support and a poorly verified WCS",
}


def explain(e: ObjectEvidence) -> str:
    """One or two sentences built from the measured values (no object-specific text)."""
    a = e.astrometry
    if a.grade == "unavailable":
        astro = "no astrometric statistics apply to the WCS used"
    else:
        astro = f"{a.grade} WCS"
        if a.gaia_matches is not None and a.r50_source in ("field_gaia", "local_gaia"):
            astro += f" ({a.gaia_matches} Gaia matches"
            astro += f", r50 {a.r50_px:.2f} px)" if a.r50_px is not None else ")"
        elif a.r50_px is not None:
            astro += f" (r50 {a.r50_px:.2f} px from {a.r50_source})"

    if e.path == PATH_COMPACT:
        c = e.compact
        if c.detection_source_id is not None:
            image = (
                f'nearest accepted detection {c.separation_arcsec:.2f}" away '
                f"({c.normalized_offset:.1f} r50, {c.offset_grade})"
            )
        elif c.grade == "unavailable":
            image = "no point-source association was possible"
        else:
            image = "no accepted detection near the catalogue position"
    else:
        x = e.extended
        visible = (
            f"{x.visible_fraction:.0%} of the extent in the image"
            if x.visible_fraction is not None
            else "extent visibility unknown"
        )
        if x.contrast is not None:
            image = (
                f"footprint contrast {x.contrast:.1f} sigma "
                f"(z {x.contrast_significance:.0f}), {visible}"
            )
            if x.structure_offset_fraction is not None:
                image += f", structure offset {x.structure_offset_fraction:.2f} radius"
        else:
            image = f"footprint contrast not measurable, {visible}"

    ambiguity = ""
    if e.ambiguity.competitors:
        names = ", ".join(c.display_name for c in e.ambiguity.competitors[:3])
        ambiguity = f"; competing catalogue objects: {names}"
    caps = f"; limited by {', '.join(e.caps)}" if e.caps else ""
    lead = {
        LEVEL_INSUFFICIENT: "Insufficient evidence (abstained)",
        LEVEL_CATALOGUE_ONLY: "Catalogue-only",
    }.get(e.support_level, f"{e.support_level.capitalize()} support")
    return f"{lead}: {astro}; {image}{ambiguity}{caps}."

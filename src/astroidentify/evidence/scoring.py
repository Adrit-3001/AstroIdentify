"""Aggregation: evidence groups -> one ordinal support level (rule-based; no weights).

Groups and how each is used, once, to avoid double counting:

1. **Image support** sets the base level. For compact objects this is the positional grade
   (which already contains the astrometric scale r50 and the chance expectation). For
   extended objects it is the contrast grade. strong/moderate/weak map to the same level.
   ``none`` (looked, nothing visible) and ``unavailable`` (could not be measured) both map
   to ``catalogue-only``. Neither is contradiction.
2. **Astrometric verification** caps the level: ``adequate`` WCS -> at most moderate,
   ``poor`` -> at most weak. ``unavailable`` -> insufficient (abstain).
3. **Catalogue**: a SIMBAD candidate type caps at moderate. Popularity (designation,
   references) is never used.
4. **Data quality** caps from the compact/extended paths (truncation, frame-filling objects,
   imprecise placement for the size, off-centre structure, low-SNR/edge/uncalibrated
   saturated sources).
5. **Ambiguity** caps: minor -> moderate, major -> weak, severe -> insufficient.

Caps only ever lower a level (``level = weakest of base and caps``). So: more ambiguity,
a larger normalized offset or a lower contrast never raise support.

Abstention (``insufficient``):
* no applicable astrometric statistics;
* an extended object mostly outside the image;
* severe ambiguity;
* ``catalogue-only`` under a poor WCS (nothing supports the placement).

``strong`` therefore requires strong image evidence, a precise WCS, a confirmed type, no
truncation and no ambiguity.
"""

from __future__ import annotations

from astroidentify.evidence.types import (
    ASTROMETRY_ADEQUATE,
    ASTROMETRY_POOR,
    ASTROMETRY_UNAVAILABLE,
    GRADE_MODERATE,
    GRADE_NONE,
    GRADE_STRONG,
    GRADE_UNAVAILABLE,
    GRADE_WEAK,
    LEVEL_CATALOGUE_ONLY,
    LEVEL_INSUFFICIENT,
    LEVEL_MODERATE,
    LEVEL_STRONG,
    LEVEL_WEAK,
    SUPPORT_LEVELS,
)

_BASE = {
    GRADE_STRONG: LEVEL_STRONG,
    GRADE_MODERATE: LEVEL_MODERATE,
    GRADE_WEAK: LEVEL_WEAK,
    GRADE_NONE: LEVEL_CATALOGUE_ONLY,
    GRADE_UNAVAILABLE: LEVEL_CATALOGUE_ONLY,
}
_AMBIGUITY_CAP = {"minor": LEVEL_MODERATE, "major": LEVEL_WEAK}


def weaker(a: str, b: str) -> str:
    return a if SUPPORT_LEVELS.index(a) >= SUPPORT_LEVELS.index(b) else b


def aggregate(
    *,
    image_grade: str,
    astrometry_grade: str,
    candidate_type: bool,
    mostly_outside: bool,
    ambiguity_severity: str,
    path_caps: list[tuple[str, str]],
) -> tuple[str, list[str], list[str]]:
    """Return ``(support_level, applied_caps, extra_reason_codes)``."""
    if astrometry_grade == ASTROMETRY_UNAVAILABLE:
        return LEVEL_INSUFFICIENT, ["ASTROMETRY_UNAVAILABLE"], ["ABSTAIN_NO_ASTROMETRY"]
    if mostly_outside:
        return LEVEL_INSUFFICIENT, ["OBJECT_MOSTLY_OUTSIDE"], ["ABSTAIN_MOSTLY_OUTSIDE"]
    if ambiguity_severity == "severe":
        return LEVEL_INSUFFICIENT, ["AMBIGUITY_SEVERE"], ["ABSTAIN_AMBIGUOUS"]

    caps = list(path_caps)
    if astrometry_grade == ASTROMETRY_ADEQUATE:
        caps.append((LEVEL_MODERATE, "ASTROMETRY_ADEQUATE"))
    elif astrometry_grade == ASTROMETRY_POOR:
        caps.append((LEVEL_WEAK, "ASTROMETRY_POOR"))
    if candidate_type:
        caps.append((LEVEL_MODERATE, "CANDIDATE_TYPE"))
    if ambiguity_severity in _AMBIGUITY_CAP:
        caps.append((_AMBIGUITY_CAP[ambiguity_severity], "AMBIGUOUS_CANDIDATES"))

    level = _BASE[image_grade]
    applied = []
    for cap_level, code in caps:
        if weaker(level, cap_level) != level:
            applied.append(code)
        level = weaker(level, cap_level)
    extra: list[str] = []
    if level == LEVEL_CATALOGUE_ONLY and astrometry_grade == ASTROMETRY_POOR:
        level = LEVEL_INSUFFICIENT
        applied.append("ASTROMETRY_POOR")
        extra.append("ABSTAIN_NO_SUPPORT")
    return level, applied, extra

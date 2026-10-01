"""Object-type policy: SIMBAD object types -> annotation categories.

The category comes from SIMBAD's own type hierarchy (``otypedef.path``, e.g.
``"ISM > Cld > GNe > RNe"``), with explicit overrides where the hierarchy groups a
non-stellar object under stars (planetary nebulae are evolved-star products in SIMBAD):

=====================  ==============================================================
category               SIMBAD types
=====================  ==============================================================
planetary_nebula       ``PN``, ``PN?``
supernova_remnant      ``SNR``, ``SR?``
nebula                 everything under ``ISM`` (H II regions, reflection/dark/diffuse
                       nebulae, clouds, bubbles, ...) and ``PoC`` (part of a cloud)
star_cluster           ``Cl*`` subtree (open/globular clusters) and stellar associations
                       (``As*``, ``As?``)
galaxy                 ``G`` subtree (galaxies, AGN, quasars, Seyferts, starbursts, ...)
galaxy_group           pairs, groups, clusters, superclusters and interacting galaxies
galaxy_part            ``PoG`` (e.g. H II regions inside a galaxy)
stellar_group_diffuse  moving groups and stellar streams (degrees across, no usable position)
star                   everything else under ``*`` (Gaia's business, Milestone 4)
other                  radio/IR/X-ray/UV sources, blends, transients, regions, ...
unknown                types missing from SIMBAD's type table
=====================  ==============================================================

``ObjectConfig.included_categories`` (default: planetary nebulae, nebulae, supernova
remnants, star clusters, galaxies, galaxy groups) decides which categories are retained
for annotation. Excluded rows stay in the raw tables with their reason. SIMBAD
"candidate" types (containing ``?``) are retained unless ``include_candidates`` is off.
"""

from __future__ import annotations

CATEGORY_PLANETARY_NEBULA = "planetary_nebula"
CATEGORY_SUPERNOVA_REMNANT = "supernova_remnant"
CATEGORY_NEBULA = "nebula"
CATEGORY_STAR_CLUSTER = "star_cluster"
CATEGORY_GALAXY = "galaxy"
CATEGORY_GALAXY_GROUP = "galaxy_group"
CATEGORY_GALAXY_PART = "galaxy_part"
CATEGORY_STELLAR_GROUP = "stellar_group_diffuse"
CATEGORY_STAR = "star"
CATEGORY_OTHER = "other"
CATEGORY_UNKNOWN = "unknown"

ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_PLANETARY_NEBULA,
    CATEGORY_SUPERNOVA_REMNANT,
    CATEGORY_NEBULA,
    CATEGORY_STAR_CLUSTER,
    CATEGORY_GALAXY,
    CATEGORY_GALAXY_GROUP,
    CATEGORY_GALAXY_PART,
    CATEGORY_STELLAR_GROUP,
    CATEGORY_STAR,
    CATEGORY_OTHER,
    CATEGORY_UNKNOWN,
)

# Explicit overrides of the hierarchy (checked first).
_BY_TYPE: dict[str, str] = {
    "PN": CATEGORY_PLANETARY_NEBULA,
    "PN?": CATEGORY_PLANETARY_NEBULA,
    "SNR": CATEGORY_SUPERNOVA_REMNANT,
    "SR?": CATEGORY_SUPERNOVA_REMNANT,
    "PoC": CATEGORY_NEBULA,
    "PoG": CATEGORY_GALAXY_PART,
    "MGr": CATEGORY_STELLAR_GROUP,
    "St*": CATEGORY_STELLAR_GROUP,
}

# Top-level node of ``otypedef.path`` -> category.
_BY_ROOT: dict[str, str] = {
    "ISM": CATEGORY_NEBULA,
    "Cl*": CATEGORY_STAR_CLUSTER,
    "As*": CATEGORY_STAR_CLUSTER,
    "G": CATEGORY_GALAXY,
    "GrG": CATEGORY_GALAXY_GROUP,
    "ClG": CATEGORY_GALAXY_GROUP,
    "PaG": CATEGORY_GALAXY_GROUP,
    "IG": CATEGORY_GALAXY_GROUP,
    "SCG": CATEGORY_GALAXY_GROUP,
    "PCG": CATEGORY_GALAXY_GROUP,
    "*": CATEGORY_STAR,
}


def categorise(otype: str | None, path: str | None) -> str:
    """Category of a SIMBAD object type (see the module docstring)."""
    if not otype:
        return CATEGORY_UNKNOWN
    if otype in _BY_TYPE:
        return _BY_TYPE[otype]
    if path is None:
        return CATEGORY_UNKNOWN
    root = path.split(">")[0].strip()
    return _BY_ROOT.get(root, CATEGORY_OTHER)


def is_candidate_type(otype: str | None) -> bool:
    """SIMBAD marks unconfirmed types with ``?`` (``"?"`` alone means unknown nature)."""
    return bool(otype) and "?" in otype and otype != "?"


def exclusion_reason(
    category: str, candidate: bool, included: tuple[str, ...], include_candidates: bool
) -> str | None:
    """Why an in-field object is not retained, or ``None`` if it is."""
    if category not in included:
        return f"category '{category}' not included"
    if candidate and not include_candidates:
        return "candidate type excluded"
    return None

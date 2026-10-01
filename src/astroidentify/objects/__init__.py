"""Milestone 5: named-object identification and annotation from the solved WCS.

WCS -> WCS-derived sky region -> SIMBAD (TAP) -> pixel projection -> type filtering ->
image association -> annotated overlay. No object name or position is ever an input.
"""

from astroidentify.objects.outputs import ObjectOutputPaths, save_object_outputs
from astroidentify.objects.pipeline import (
    IdentifyInputs,
    identify_objects,
    load_identify_inputs,
    select_wcs,
)
from astroidentify.objects.simbad import SimbadProvider
from astroidentify.objects.types import CatalogObject, IdentificationResult

__all__ = [
    "CatalogObject",
    "IdentificationResult",
    "IdentifyInputs",
    "ObjectOutputPaths",
    "SimbadProvider",
    "identify_objects",
    "load_identify_inputs",
    "save_object_outputs",
    "select_wcs",
]

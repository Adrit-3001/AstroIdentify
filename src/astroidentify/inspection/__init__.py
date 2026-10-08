"""Milestone 6.1: display-only inspection of an already-identified object.

Nothing in the scientific pipeline imports this package; its stretched pixels go only to
inspection PNGs.
"""

from astroidentify.inspection.pipeline import InspectionOptions, InspectionResult, inspect_object
from astroidentify.inspection.selection import ObjectSelectionError, select_object
from astroidentify.inspection.stretch import STRETCHES, StretchParameters, stretch

__all__ = [
    "STRETCHES",
    "InspectionOptions",
    "InspectionResult",
    "ObjectSelectionError",
    "StretchParameters",
    "inspect_object",
    "select_object",
    "stretch",
]

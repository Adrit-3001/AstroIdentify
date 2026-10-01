"""Milestone 3: blind astrometric plate solving (WCS) from detected stellar sources.

Plate solving determines where an image points on the sky. It does not identify objects.
"""

from astroidentify.astrometry.outputs import AstrometryOutputPaths, save_astrometry_outputs
from astroidentify.astrometry.pipeline import plan_attempts, plate_solve
from astroidentify.astrometry.selection import select_plate_sources
from astroidentify.astrometry.types import (
    MODE_BLIND,
    MODE_SCALE_CONSTRAINED,
    PlateSolution,
    SelectedSource,
    SourceSelection,
    WcsGeometry,
)
from astroidentify.astrometry.wcs import describe_wcs, load_wcs, pixel_to_sky, sky_to_pixel
from astroidentify.astrometry.xylist import from_solver_pixels, to_solver_pixels, write_xylist

__all__ = [
    "MODE_BLIND",
    "MODE_SCALE_CONSTRAINED",
    "AstrometryOutputPaths",
    "PlateSolution",
    "SelectedSource",
    "SourceSelection",
    "WcsGeometry",
    "describe_wcs",
    "from_solver_pixels",
    "load_wcs",
    "pixel_to_sky",
    "plan_attempts",
    "plate_solve",
    "save_astrometry_outputs",
    "select_plate_sources",
    "sky_to_pixel",
    "to_solver_pixels",
    "write_xylist",
]

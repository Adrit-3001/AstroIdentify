"""Milestone 2: stellar source detection on preprocessed images.

Detection finds and measures point-source candidates. It does not identify objects.
"""

from astroidentify.detection.background import estimate_local_background
from astroidentify.detection.detector import estimate_fwhm, find_candidates
from astroidentify.detection.filtering import REJECTION_REASONS, apply_filters
from astroidentify.detection.measurements import measure_sources
from astroidentify.detection.outputs import DetectionOutputPaths, save_detection_outputs
from astroidentify.detection.overlay import render_overlay
from astroidentify.detection.pipeline import detect_image, detect_sources
from astroidentify.detection.plane import make_detection_plane
from astroidentify.detection.saturation import (
    consolidate_saturated_cores,
    resolve_saturation_level,
    saturated_pixel_mask,
)
from astroidentify.detection.types import (
    COORDINATE_CONVENTION,
    DetectionPlane,
    DetectionResult,
    FwhmEstimate,
    LocalBackground,
    Source,
)

__all__ = [
    "COORDINATE_CONVENTION",
    "REJECTION_REASONS",
    "DetectionOutputPaths",
    "DetectionPlane",
    "DetectionResult",
    "FwhmEstimate",
    "LocalBackground",
    "Source",
    "apply_filters",
    "consolidate_saturated_cores",
    "detect_image",
    "detect_sources",
    "estimate_fwhm",
    "estimate_local_background",
    "find_candidates",
    "make_detection_plane",
    "measure_sources",
    "render_overlay",
    "resolve_saturation_level",
    "saturated_pixel_mask",
    "save_detection_outputs",
]

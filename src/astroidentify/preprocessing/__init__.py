"""Milestone 1: image ingestion, background/noise estimation, normalization and previews."""

from astroidentify.preprocessing.background import (
    analysis_plane,
    estimate_background,
    estimate_difference_noise,
)
from astroidentify.preprocessing.loader import SUPPORTED_EXTENSIONS, image_from_array, load_image
from astroidentify.preprocessing.normalize import compute_normalization, normalize
from astroidentify.preprocessing.outputs import OutputPaths, build_metadata, save_outputs
from astroidentify.preprocessing.pipeline import preprocess, preprocess_image
from astroidentify.preprocessing.preview import render_preview, save_preview

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "OutputPaths",
    "analysis_plane",
    "build_metadata",
    "compute_normalization",
    "estimate_background",
    "estimate_difference_noise",
    "image_from_array",
    "load_image",
    "normalize",
    "preprocess",
    "preprocess_image",
    "render_preview",
    "save_outputs",
    "save_preview",
]

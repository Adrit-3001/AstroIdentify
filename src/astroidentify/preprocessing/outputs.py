"""Writing preprocessing artifacts and serializing metadata.

All on-disk formats are defined here so the schema can evolve in one place.

Artifacts written to the output directory:

* ``processed.npy``  - the normalized float32 array (same shape as the source image).
* ``preview.png``    - an 8-bit display preview (not for analysis).
* ``metadata.json``  - source information, estimates, normalization and diagnostics.
* ``valid_mask.npy`` - boolean ``(H, W)`` mask, only written if some pixels were invalid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify import __version__
from astroidentify.preprocessing.preview import resolve_stretch, save_preview
from astroidentify.serialization import (
    ensure_not_source,
    prepare_output_dir,
    remove_file,
    save_array,
    sha256_file,
    to_jsonable,
    write_json,
)
from astroidentify.types import BackgroundEstimate, PreprocessingResult

logger = logging.getLogger(__name__)

METADATA_SCHEMA_VERSION = 1
PROCESSED_FILENAME = "processed.npy"
PREVIEW_FILENAME = "preview.png"
METADATA_FILENAME = "metadata.json"
MASK_FILENAME = "valid_mask.npy"
DEFAULT_OUTPUT_ROOT = Path("outputs")


@dataclass(frozen=True)
class OutputPaths:
    """Locations of the artifacts written by :func:`save_outputs`."""

    directory: Path
    processed: Path
    preview: Path
    metadata: Path
    mask: Path | None


def default_output_dir(source: str | Path, root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    """Return ``root/<image name without extensions>`` (``m57.fits.gz`` -> ``m57``)."""
    name = Path(source).name
    stem = name.split(".", 1)[0] or name
    return root / stem


def save_outputs(result: PreprocessingResult, output_dir: str | Path) -> OutputPaths:
    """Write the processed array, preview and metadata for ``result`` to ``output_dir``.

    Existing artifacts from earlier runs in the same directory are replaced.

    Raises:
        OutputError: If the directory cannot be created, an artifact would overwrite the
            source image, or a file cannot be written.
    """
    directory = prepare_output_dir(output_dir)
    has_invalid = not bool(np.all(result.valid_mask))
    paths = OutputPaths(
        directory=directory,
        processed=directory / PROCESSED_FILENAME,
        preview=directory / PREVIEW_FILENAME,
        metadata=directory / METADATA_FILENAME,
        mask=directory / MASK_FILENAME if has_invalid else None,
    )
    stale_mask = directory / MASK_FILENAME
    ensure_not_source(
        (paths.processed, paths.preview, paths.metadata, stale_mask), result.image.source_path
    )

    save_array(paths.processed, result.normalized)
    if paths.mask is not None:
        save_array(paths.mask, result.valid_mask)
    elif stale_mask.exists():
        # A mask from an earlier run would contradict this run's metadata.
        remove_file(stale_mask)
    save_preview(result.image, paths.preview, result.config)
    write_json(paths.metadata, build_metadata(result, paths))
    logger.info("Wrote outputs to %s", directory)
    return paths


def build_metadata(result: PreprocessingResult, paths: OutputPaths | None = None) -> dict[str, Any]:
    """Build the JSON-serializable metadata document for ``result``."""
    image = result.image
    norm = result.normalization
    config = result.config
    return to_jsonable(
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "software": {"name": "astroidentify", "version": __version__},
            "source": image.source_path.name,
            "source_path": str(image.source_path),
            "source_sha256": sha256_file(image.source_path),
            "format": image.format.value,
            "width": image.width,
            "height": image.height,
            "channels": image.channels,
            "original_shape": list(image.original_shape),
            "original_dtype": image.original_dtype,
            "array_layout": {
                "axes": ["y", "x", "channel"] if image.is_color else ["y", "x"],
                "dtype": str(result.normalized.dtype),
                "row_zero_displayed_at": "bottom" if image.display_origin == "lower" else "top",
            },
            "background_level": result.background_level,
            "noise_sigma": result.noise_sigma,
            "background": {
                **_background_dict(result.background),
                "plane": "channel_mean" if image.is_color else "image",
                "per_channel": [_background_dict(b) for b in result.channel_backgrounds],
            },
            "normalization": {
                "method": norm.method,
                "formula": "(x - lower_value) / (upper_value - lower_value)",
                "lower_percentile": norm.lower_percentile,
                "upper_percentile": norm.upper_percentile,
                "lower_value": norm.lower_value,
                "upper_value": norm.upper_value,
                "clipped": norm.clip,
                "degenerate": norm.degenerate,
                "invalid_pixel_fill": "normalized_background_level",
                "normalized_background_level": result.normalized_background_level,
                "normalized_noise_sigma": result.normalized_noise_sigma,
            },
            "preview": {
                "stretch": resolve_stretch(image, config),
                "lower_percentile": config.preview_lower_percentile,
                "upper_percentile": config.preview_upper_percentile,
                "asinh_softening": config.preview_asinh_softening,
                "max_dimension": config.preview_max_dimension,
                "flipped_vertically": image.display_origin == "lower",
            },
            "diagnostics": result.diagnostics,
            "source_metadata": image.metadata,
            "config": config.to_dict(),
            "artifacts": _artifact_names(paths),
        }
    )


def _background_dict(estimate: BackgroundEstimate) -> dict[str, Any]:
    return {
        "level": estimate.level,
        "noise_sigma": estimate.noise_sigma,
        "method": estimate.method,
        "noise_method": estimate.noise_method,
        "n_pixels": estimate.n_pixels,
        "n_retained": estimate.n_retained,
        "rejected_fraction": estimate.rejected_fraction,
    }


def _artifact_names(paths: OutputPaths | None) -> dict[str, str | None]:
    if paths is None:
        return {}
    return {
        "processed": paths.processed.name,
        "preview": paths.preview.name,
        "metadata": paths.metadata.name,
        "valid_mask": paths.mask.name if paths.mask else None,
    }

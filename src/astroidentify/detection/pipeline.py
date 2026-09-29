"""Milestone 2 pipeline.

detection plane -> local background -> FWHM -> DAOFIND -> saturated-core consolidation ->
measurement -> numbering by brightness -> filtering.

Typical use::

    preprocessing = preprocess_image("data/raw/field.png")
    detection = detect_sources(preprocessing)
    stars = detection.xy_flux()   # (N, 3) x, y, flux of accepted sources, brightest first
"""

from __future__ import annotations

import dataclasses
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify.config import DetectionConfig, PreprocessingConfig
from astroidentify.detection.background import (
    estimate_local_background,
    map_statistics,
    quantization_rms,
)
from astroidentify.detection.detector import estimate_fwhm, find_candidates
from astroidentify.detection.filtering import apply_filters
from astroidentify.detection.measurements import empirical_noise_factor, measure_sources
from astroidentify.detection.plane import make_detection_plane
from astroidentify.detection.saturation import (
    consolidate_saturated_cores,
    resolve_saturation_level,
    saturated_pixel_mask,
)
from astroidentify.detection.types import (
    DetectionResult,
    LocalBackground,
    Source,
    brightness_key,
)
from astroidentify.preprocessing.pipeline import preprocess_image
from astroidentify.types import PreprocessingResult

logger = logging.getLogger(__name__)


def detect_sources(
    preprocessing: PreprocessingResult, config: DetectionConfig | None = None
) -> DetectionResult:
    """Detect, measure and filter stellar source candidates in a preprocessed image.

    The preprocessing result is only read, never modified or reloaded.
    """
    config = config or DetectionConfig()
    image = preprocessing.image
    warnings: list[str] = []

    plane = make_detection_plane(preprocessing)
    background = estimate_local_background(
        plane, config, quantization_rms=quantization_rms(image.original_dtype, image.channels)
    )
    warnings += background.warnings

    saturation_level, saturation_source = resolve_saturation_level(image, config)
    if saturation_level is None:
        _note(warnings, "saturation level unknown; saturated sources cannot be flagged")
    saturated_pixels = saturated_pixel_mask(image, saturation_level)

    fwhm, fwhm_notes = estimate_fwhm(background, plane, saturated_pixels, config)
    warnings += fwhm_notes
    radius = config.aperture_radius_fwhm * fwhm.value

    candidates = find_candidates(
        background, plane, fwhm.value, config.detection_sigma, config.min_separation_fwhm
    )

    noise_factor, n_empty = 1.0, 0
    if config.correct_correlated_noise:
        noise_factor, n_empty = empirical_noise_factor(
            background,
            candidates.xy,
            radius,
            saturated_pixels | plane.invalid_mask,
            config.empty_aperture_min_count,
        )
        if n_empty < config.empty_aperture_min_count:
            _note(
                warnings,
                f"only {n_empty} source-free apertures; correlated-noise correction skipped",
            )

    sources: tuple[Source, ...] = ()
    reference_fwhm = None
    if len(candidates) == 0:
        _note(warnings, "no source candidates found above the detection threshold")
    else:
        cores = consolidate_saturated_cores(
            candidates.xy, candidates.flux, candidates.method, saturated_pixels, fwhm.value
        )
        measured = measure_sources(
            candidates,
            cores.xy,
            cores.centroid_method,
            cores.core_axis_ratio,
            cores.core_area,
            plane,
            background,
            saturated_pixels,
            fwhm.value,
            noise_factor,
            config,
        )
        numbered = _number_by_brightness(measured, cores.primary_index)
        filtered, reference_fwhm = apply_filters(numbered, fwhm.value, config)
        sources = tuple(filtered)
        if not any(s.accepted for s in sources):
            _note(warnings, "no candidates passed filtering")

    diagnostics = _diagnostics(sources, background)
    diagnostics.update(
        fwhm_used=fwhm.value,
        reference_effective_fwhm=reference_fwhm,
        noise_correlation_factor=noise_factor,
        n_empty_apertures=n_empty,
        warnings=warnings,
    )
    logger.info(
        "%d candidates, %d accepted (FWHM %.3g px, noise factor %.3g)",
        len(sources),
        diagnostics["n_accepted"],
        fwhm.value,
        noise_factor,
    )
    return DetectionResult(
        preprocessing=preprocessing,
        plane=plane,
        background=background,
        sources=sources,
        fwhm=fwhm,
        aperture_radius=radius,
        noise_factor=noise_factor,
        saturation_level=saturation_level,
        saturation_source=saturation_source,
        config=config,
        diagnostics=diagnostics,
    )


def detect_image(
    path: str | Path,
    preprocessing_config: PreprocessingConfig | None = None,
    detection_config: DetectionConfig | None = None,
) -> DetectionResult:
    """Convenience wrapper: preprocess ``path`` (Milestone 1), then detect sources."""
    return detect_sources(preprocess_image(path, preprocessing_config), detection_config)


def _number_by_brightness(measured: list[Source], primary_index: np.ndarray) -> list[Source]:
    """Order by decreasing flux, assign 1-based IDs and resolve ``duplicate_of`` to IDs."""
    order = sorted(range(len(measured)), key=lambda i: brightness_key(measured[i]))
    new_id = {old: rank for rank, old in enumerate(order, start=1)}
    return [
        dataclasses.replace(
            measured[i],
            source_id=new_id[i],
            duplicate_of=new_id[int(primary_index[i])] if primary_index[i] >= 0 else None,
        )
        for i in order
    ]


def _note(warnings: list[str], message: str) -> None:
    logger.warning(message)
    warnings.append(message)


def _median(values: list[float]) -> float | None:
    finite = [v for v in values if np.isfinite(v)]
    return float(np.median(finite)) if finite else None


def _diagnostics(sources: tuple[Source, ...], background: LocalBackground) -> dict[str, Any]:
    accepted = [s for s in sources if s.accepted]
    reasons = Counter(reason for s in sources for reason in s.rejection_reasons)
    return {
        "n_candidates": len(sources),
        "n_accepted": len(accepted),
        "n_rejected": len(sources) - len(accepted),
        "n_saturated": sum(s.saturated for s in sources),
        "n_saturated_accepted": sum(s.saturated for s in accepted),
        "n_edge_flagged": sum(s.edge for s in sources),
        "n_edge_flagged_accepted": sum(s.edge for s in accepted),
        "rejection_reason_counts": dict(sorted(reasons.items())),
        "median_snr_accepted": _median([s.snr for s in accepted]),
        "median_fwhm_accepted": _median([s.fwhm for s in accepted if not s.saturated]),
        "background_map": map_statistics(background.background),
        "background_rms_map": map_statistics(background.rms),
    }

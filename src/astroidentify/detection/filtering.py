"""Explainable, conservative candidate filtering.

Every candidate is kept in the output; filtering only sets ``accepted`` and records one or
more reason codes. Thresholds come from :class:`~astroidentify.config.DetectionConfig`.

Saturated sources are exempt from the DAOFIND shape rules (sharpness, roundness, width):
clipped cores make those statistics unreliable. Instead, a saturated source is rejected if
the saturated region it sits on is strongly elongated: saturated stellar cores are compact
even when the stellar wings are distorted, while saturated extended structure is not.
Saturation alone never rejects a source.

Candidates from the supplementary peak search have no DAOFIND statistics, so the sharpness
and roundness rules are skipped for them; the SNR, edge, width and saturation rules apply.

The width rule compares each effective FWHM with the median effective FWHM of the
unsaturated candidates that pass the SNR cut, so like is compared with like.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from astroidentify.config import DetectionConfig
from astroidentify.detection.types import Source

LOW_SNR = "low_snr"
TOO_CLOSE_TO_EDGE = "too_close_to_edge"
NOT_STAR_LIKE = "not_star_like"
TOO_ELONGATED = "too_elongated"
EXTENDED = "extended"
DUPLICATE_SATURATED = "duplicate_of_saturated_source"
ELONGATED_SATURATED_REGION = "elongated_saturated_region"

REJECTION_REASONS: dict[str, str] = {
    LOW_SNR: "aperture SNR below min_snr",
    TOO_CLOSE_TO_EDGE: "centroid closer than edge_reject_fwhm * FWHM to the image border",
    NOT_STAR_LIKE: "DAOFIND sharpness outside sharpness_range (unsaturated sources only)",
    TOO_ELONGATED: "|roundness1| or |roundness2| above max_abs_roundness (unsaturated only)",
    ELONGATED_SATURATED_REGION: "saturated region wider than saturated_shape_min_diameter_fwhm "
    "* FWHM with axis ratio below min_saturated_axis_ratio (saturated sources only)",
    EXTENDED: "effective FWHM above max_fwhm_ratio * median effective FWHM of stars "
    "(unsaturated sources only)",
    DUPLICATE_SATURATED: "another detection on the same saturated core (see duplicate_of)",
}

# Minimum number of reference stars for the width rule; below this it is skipped.
_MIN_REFERENCE_STARS = 5


def reference_width(sources: list[Source], config: DetectionConfig) -> float | None:
    """Median effective FWHM of unsaturated, non-duplicate candidates passing the SNR cut."""
    widths = [
        s.fwhm
        for s in sources
        if not s.saturated
        and s.duplicate_of is None
        and s.snr >= config.min_snr
        and math.isfinite(s.fwhm)
    ]
    return float(np.median(widths)) if len(widths) >= _MIN_REFERENCE_STARS else None


def rejection_reasons(
    source: Source, fwhm: float, reference_fwhm: float | None, config: DetectionConfig
) -> tuple[str, ...]:
    """Return the reason codes ``source`` fails (empty tuple if it passes)."""
    reasons: list[str] = []
    if source.duplicate_of is not None:
        reasons.append(DUPLICATE_SATURATED)
    if not source.snr >= config.min_snr:  # also catches NaN
        reasons.append(LOW_SNR)
    if source.edge_distance < config.edge_reject_fwhm * fwhm:
        reasons.append(TOO_CLOSE_TO_EDGE)
    if source.saturated:
        # Shape is only meaningful for regions at least as wide as a stellar core; a few
        # clipped pixels are elongated by pixelization alone.
        diameter = 2.0 * math.sqrt(source.saturated_core_area / math.pi)
        if (
            diameter >= config.saturated_shape_min_diameter_fwhm * fwhm
            and source.saturated_core_axis_ratio < config.min_saturated_axis_ratio
        ):
            reasons.append(ELONGATED_SATURATED_REGION)
    else:
        # DAOFIND statistics are NaN (not measured) for supplementary peaks; skip them then.
        low, high = config.sharpness_range
        if math.isfinite(source.sharpness) and not low <= source.sharpness <= high:
            reasons.append(NOT_STAR_LIKE)
        roundness = max(abs(source.roundness1), abs(source.roundness2))
        if math.isfinite(roundness) and roundness > config.max_abs_roundness:
            reasons.append(TOO_ELONGATED)
        if (
            reference_fwhm is not None
            and math.isfinite(source.fwhm)
            and source.fwhm > config.max_fwhm_ratio * reference_fwhm
        ):
            reasons.append(EXTENDED)
    return tuple(reasons)


def apply_filters(
    sources: list[Source], fwhm: float, config: DetectionConfig
) -> tuple[list[Source], float | None]:
    """Return ``(filtered sources, reference effective FWHM)``; order is preserved."""
    reference = reference_width(sources, config)
    filtered = []
    for source in sources:
        reasons = rejection_reasons(source, fwhm, reference, config)
        filtered.append(
            dataclasses.replace(source, accepted=not reasons, rejection_reasons=reasons)
        )
    return filtered, reference

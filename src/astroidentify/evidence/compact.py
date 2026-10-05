"""Image evidence for compact objects (no catalogued size, or small): point-source association.

* Association: the nearest accepted Milestone 2 detection (astrometric centroid; ties to
  the lower ``source_id``), kept if its normalized offset ``n = separation / r50`` is at
  most ``max_association_offset``.
* Offset grade (Rayleigh statistics of the matched-star residuals): ``close`` if
  ``n <= close_offset`` (79 % of true pairs for 1.5), ``consistent`` if
  ``n <= consistent_offset`` (99.8 % for 3), else ``poor``.
* Discrimination: the expected number of *unrelated* accepted detections inside the
  ``consistent_offset * r50`` region (field detection density x area). A poorer WCS has a
  larger r50 and therefore a larger chance expectation, so the same separation gives less
  support.
* Grade: close and chance <= ``chance_strong`` -> strong; close or consistent with chance
  <= ``chance_moderate`` -> moderate; otherwise weak. No associated detection -> ``none``
  (absence of a detection, not contradiction). No detection catalogue -> ``unavailable``.

Source-quality caps (applied in ``scoring``): low SNR, an edge-flagged detection, or a
saturated detection without a calibrated astrometric centroid limit support to moderate.
A Gaia counterpart of the detection is recorded as context only. It cannot tell a compact
galaxy from a foreground star, so it is not scored.
"""

from __future__ import annotations

import math

from astroidentify.config import EvidenceConfig
from astroidentify.detection.types import Source
from astroidentify.evidence.types import (
    GRADE_MODERATE,
    GRADE_NONE,
    GRADE_STRONG,
    GRADE_UNAVAILABLE,
    GRADE_WEAK,
    LEVEL_MODERATE,
    CompactImageEvidence,
)

OFFSET_CLOSE = "close"
OFFSET_CONSISTENT = "consistent"
OFFSET_POOR = "poor"


def assess_compact(
    x: float,
    y: float,
    r50_px: float | None,
    detections: list[Source] | None,
    detection_density: float | None,
    gaia_for_detection: dict[int, int],
    pixel_scale: float,
    config: EvidenceConfig,
) -> tuple[CompactImageEvidence, list[str], list[tuple[str, str]]]:
    """Return ``(evidence, reason_codes, caps)`` for a compact object at ``(x, y)``."""
    empty = dict(
        detection_source_id=None,
        separation_px=None,
        separation_arcsec=None,
        normalized_offset=None,
        offset_grade=None,
        chance_expectation=None,
        source_snr=None,
        source_saturated=None,
        source_edge=None,
        source_astrometric_method=None,
        detection_gaia_source_id=None,
    )
    if detections is None:
        return CompactImageEvidence(**empty, grade=GRADE_UNAVAILABLE), ["MISSING_DETECTIONS"], []
    if r50_px is None:
        return (
            CompactImageEvidence(**empty, grade=GRADE_UNAVAILABLE),
            ["MISSING_POSITION_SCALE"],
            [],
        )

    chance = None
    if detection_density is not None:
        chance = detection_density * math.pi * (config.consistent_offset * r50_px) ** 2
    best = None
    for source in detections:
        sx, sy = source.astrometric_xy
        candidate = (math.hypot(sx - x, sy - y), source.source_id, source)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is None or best[0] / r50_px > config.max_association_offset:
        evidence = CompactImageEvidence(**{**empty, "chance_expectation": chance}, grade=GRADE_NONE)
        return evidence, ["NO_IMAGE_ASSOCIATION"], []

    separation, _, source = best
    n = separation / r50_px
    if n <= config.close_offset:
        offset_grade, codes = OFFSET_CLOSE, ["POSITION_MATCH_CLOSE"]
    elif n <= config.consistent_offset:
        offset_grade, codes = OFFSET_CONSISTENT, ["POSITION_MATCH_CONSISTENT"]
    else:
        offset_grade, codes = OFFSET_POOR, ["POSITION_MATCH_POOR"]

    discriminating = chance is not None and chance <= config.chance_moderate
    if offset_grade == OFFSET_POOR:
        grade = GRADE_WEAK
    elif not discriminating:
        grade = GRADE_WEAK
        codes.append("POSITION_NOT_DISCRIMINATING")
    elif offset_grade == OFFSET_CLOSE and chance <= config.chance_strong:
        grade = GRADE_STRONG
    else:
        grade = GRADE_MODERATE

    caps: list[tuple[str, str]] = []
    if not source.snr >= config.min_source_snr:  # NaN SNR also counts as low
        codes.append("SOURCE_LOW_SNR")
        caps.append((LEVEL_MODERATE, "SOURCE_LOW_SNR"))
    if source.edge:
        codes.append("SOURCE_EDGE")
        caps.append((LEVEL_MODERATE, "SOURCE_EDGE"))
    if source.saturated:
        if source.astrometric_method == "isophote_calibrated":
            codes.append("SOURCE_SATURATED_CALIBRATED")
        else:
            codes.append("SOURCE_SATURATED_UNCALIBRATED")
            caps.append((LEVEL_MODERATE, "SOURCE_SATURATED_UNCALIBRATED"))
    gaia_id = gaia_for_detection.get(source.source_id)
    if gaia_id is not None:
        codes.append("DETECTION_GAIA_MATCHED")
    evidence = CompactImageEvidence(
        detection_source_id=source.source_id,
        separation_px=separation,
        separation_arcsec=separation * pixel_scale,
        normalized_offset=n,
        offset_grade=offset_grade,
        chance_expectation=chance,
        source_snr=float(source.snr),
        source_saturated=bool(source.saturated),
        source_edge=bool(source.edge),
        source_astrometric_method=source.astrometric_method,
        detection_gaia_source_id=gaia_id,
        grade=grade,
    )
    return evidence, codes, caps

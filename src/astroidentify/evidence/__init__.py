"""Milestone 6: deterministic, explainable evidence assessment of identified objects.

Saved Milestone 2-5 products -> astrometric, catalogue, image and ambiguity evidence ->
an ordinal support level (strong / moderate / weak / catalogue-only / insufficient) with
reason codes. Support levels are not probabilities.
"""

from astroidentify.evidence.features import EvidenceInputs, load_evidence_inputs
from astroidentify.evidence.outputs import EvidenceOutputPaths, save_evidence_outputs
from astroidentify.evidence.pipeline import assess_evidence, assess_objects
from astroidentify.evidence.types import (
    EVIDENCE_VERSION,
    SUPPORT_LEVELS,
    EvidenceResult,
    ObjectEvidence,
)

__all__ = [
    "EVIDENCE_VERSION",
    "SUPPORT_LEVELS",
    "EvidenceInputs",
    "EvidenceOutputPaths",
    "EvidenceResult",
    "ObjectEvidence",
    "assess_evidence",
    "assess_objects",
    "load_evidence_inputs",
    "save_evidence_outputs",
]

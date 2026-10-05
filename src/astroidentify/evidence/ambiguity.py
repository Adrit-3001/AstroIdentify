"""Ambiguity: other retained catalogue objects that the image evidence cannot tell apart.

Relations between retained objects are computed in canonical pixels:

* **compact vs compact.** Catalogue separation below ``compact_competitor_offset * r50``
  makes the other object a competitor for the same image source. Below
  ``compact_indistinguishable_offset * r50`` (both would be "close" to it) it is
  indistinguishable.
* **extended vs extended at comparable scale** (radius ratio at most
  ``extended_size_ratio``, one centre inside the other footprint): competitor.
  Indistinguishable when the centres are within ``extended_indistinguishable_fraction`` of
  the smaller radius and the radius ratio is at most
  ``extended_indistinguishable_size_ratio``.
* **nested** (a much smaller object, or a compact object, inside an extended footprint):
  ``contains``/``within``. This is context, not competition. An H II region inside a
  galaxy does not compete with the galaxy.

Severity: ``none``; ``minor`` (competitors exist, all distinguishable); ``major`` (at
least one indistinguishable); ``severe`` (at least ``severe_ambiguity_count``
indistinguishable). Scoring caps these at moderate / weak / insufficient, so more ambiguity
never increases support. No winner is chosen.
"""

from __future__ import annotations

import numpy as np

from astroidentify.config import EvidenceConfig
from astroidentify.evidence.types import (
    PATH_EXTENDED,
    AmbiguityEvidence,
    Competitor,
)

MAX_RELATED_LISTED = 20

SEVERITY_ORDER = ("none", "minor", "major", "severe")


def assess_ambiguity(records: list[dict], config: EvidenceConfig) -> dict[int, AmbiguityEvidence]:
    """``records``: dicts with ``id, name, type, path, x, y, radius_px, r50_px``."""
    n = len(records)
    result: dict[int, AmbiguityEvidence] = {}
    if n == 0:
        return result
    x = np.array([r["x"] for r in records], float)
    y = np.array([r["y"] for r in records], float)
    radius = np.array([r["radius_px"] or 0.0 for r in records], float)
    extended = np.array([r["path"] == PATH_EXTENDED for r in records], bool)
    for i, rec in enumerate(records):
        d = np.hypot(x - x[i], y - y[i])
        d[i] = np.inf
        competitors: list[Competitor] = []
        related: list[Competitor] = []
        if extended[i]:
            ri = radius[i]
            for j in np.flatnonzero(d < ri + radius):
                if not extended[j]:
                    if d[j] < ri:
                        related.append(_entry(records[j], d[j], "contains"))
                    continue
                rj = radius[j]
                small, large = min(ri, rj), max(ri, rj)
                ratio = large / small if small > 0 else np.inf
                if ratio <= config.extended_size_ratio and d[j] < large:
                    indistinct = (
                        d[j] < config.extended_indistinguishable_fraction * small
                        and ratio <= config.extended_indistinguishable_size_ratio
                    )
                    competitors.append(
                        _entry(
                            records[j], d[j], "indistinguishable" if indistinct else "competitor"
                        )
                    )
                elif d[j] < large:
                    related.append(_entry(records[j], d[j], "contains" if rj < ri else "within"))
        else:
            r50 = rec["r50_px"]
            if r50 is not None:
                for j in np.flatnonzero(~extended & (d < config.compact_competitor_offset * r50)):
                    indistinct = d[j] <= config.compact_indistinguishable_offset * r50
                    competitors.append(
                        _entry(
                            records[j], d[j], "indistinguishable" if indistinct else "competitor"
                        )
                    )
            for j in np.flatnonzero(extended & (d < radius)):
                related.append(_entry(records[j], d[j], "within"))
        n_indistinct = sum(c.relation == "indistinguishable" for c in competitors)
        if n_indistinct >= config.severe_ambiguity_count:
            severity = "severe"
        elif n_indistinct:
            severity = "major"
        elif competitors:
            severity = "minor"
        else:
            severity = "none"
        competitors.sort(key=lambda c: (c.separation_px, c.catalogue_id))
        related.sort(key=lambda c: (c.separation_px, c.catalogue_id))
        result[rec["id"]] = AmbiguityEvidence(
            severity=severity,
            competitors=tuple(competitors),
            related=tuple(related[:MAX_RELATED_LISTED]),
            n_related=len(related),
        )
    return result


def _entry(record: dict, separation: float, relation: str) -> Competitor:
    return Competitor(record["id"], record["name"], record["type"], float(separation), relation)

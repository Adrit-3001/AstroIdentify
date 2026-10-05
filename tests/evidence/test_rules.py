"""Aggregation rules, monotonicity, ambiguity and determinism."""

from __future__ import annotations

import itertools

import pytest

from astroidentify.config import EvidenceConfig
from astroidentify.evidence.scoring import aggregate, weaker
from astroidentify.evidence.types import SUPPORT_LEVELS
from astroidentify.exceptions import ConfigurationError
from tests.evidence.fixtures import FAR_DETECTION, detections, field, obj, plane, run

GRADES = ("strong", "moderate", "weak", "none", "unavailable")
ASTRO = ("precise", "adequate", "poor")
SEVERITY = ("none", "minor", "major", "severe")


def rank(level: str) -> int:
    return SUPPORT_LEVELS.index(level)  # 0 = strong ... 4 = insufficient


def _level(**kwargs) -> str:
    base = dict(
        image_grade="strong",
        astrometry_grade="precise",
        candidate_type=False,
        mostly_outside=False,
        ambiguity_severity="none",
        path_caps=[],
    )
    return aggregate(**{**base, **kwargs})[0]


def test_support_level_mapping_and_minimum_conditions_for_strong() -> None:
    assert [_level(image_grade=g) for g in GRADES] == [
        "strong",
        "moderate",
        "weak",
        "catalogue-only",
        "catalogue-only",
    ]
    assert _level(astrometry_grade="adequate") == "moderate"
    assert _level(astrometry_grade="poor") == "weak"
    assert _level(candidate_type=True) == "moderate"
    assert _level(path_caps=[("moderate", "OBJECT_TRUNCATED")]) == "moderate"
    assert _level(ambiguity_severity="minor") == "moderate"
    assert _level(astrometry_grade="unavailable") == "insufficient"
    assert _level(mostly_outside=True) == "insufficient"
    assert _level(image_grade="none", astrometry_grade="poor") == "insufficient"


def test_caps_never_raise_support() -> None:
    for grade, astro, severity, candidate in itertools.product(GRADES, ASTRO, SEVERITY, (0, 1)):
        level = _level(
            image_grade=grade,
            astrometry_grade=astro,
            ambiguity_severity=severity,
            candidate_type=bool(candidate),
        )
        assert rank(level) >= rank(_level(image_grade=grade))


def test_more_ambiguity_never_increases_support() -> None:
    for grade in GRADES:
        ranks = [rank(_level(image_grade=grade, ambiguity_severity=s)) for s in SEVERITY]
        assert ranks == sorted(ranks)


def test_better_astrometry_never_reduces_support() -> None:
    for grade in GRADES:
        ranks = [rank(_level(image_grade=grade, astrometry_grade=a)) for a in ASTRO]
        assert ranks == sorted(ranks)


def test_smaller_normalized_offset_never_reduces_positional_support() -> None:
    ranks = []
    for offset in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 3.5, 5.0, 6.0):
        dets = detections([(100 + offset, 100.0), FAR_DETECTION])
        ranks.append(rank(run([obj(1, 100, 100)], dets=dets)[0].support_level))
    assert ranks == sorted(ranks)
    assert ranks[0] == rank("strong") and ranks[-1] == rank("catalogue-only")


def test_stronger_extended_contrast_never_reduces_image_support() -> None:
    ranks = []
    for amplitude in (0.0, 0.5, 1.5, 3.0, 6.0, 12.0, 30.0):
        image = plane(discs=[(200, 150, 20, amplitude)])
        ranks.append(
            rank(run([obj(1, 200, 150, radius_px=20)], image=image, dets=[])[0].support_level)
        )
    assert ranks == sorted(ranks, reverse=True)
    assert ranks[0] == rank("catalogue-only") and ranks[-1] == rank("strong")


def test_same_separation_gets_less_support_under_a_poorer_wcs() -> None:
    dets = detections(
        [(101.5, 100.0)] + [(20 + 37 * i, 20 + 26 * j) for i in range(10) for j in range(10)]
    )
    good = run([obj(1, 100, 100)], dets=dets, astrometry=field(r50=1.0))[0]
    worse = run([obj(1, 100, 100)], dets=dets, astrometry=field(grade="adequate", r50=2.5, n=12))[0]
    assert good.compact.separation_px == worse.compact.separation_px == pytest.approx(1.5)
    assert good.compact.chance_expectation < worse.compact.chance_expectation
    assert rank(good.support_level) < rank(worse.support_level)


def test_nested_objects_are_context_not_competitors() -> None:
    image = plane(discs=[(200, 150, 60, 20.0)])
    galaxy = obj(1, 200, 150, radius_px=60)
    region = obj(2, 230, 150, radius_px=5, otype="HII", category="nebula")
    compact = obj(3, 170, 160)
    results = {e.catalogue_id: e for e in run([galaxy, region, compact], image=image, dets=[])}
    assert results[1].ambiguity.severity == "none"
    assert {r.relation for r in results[1].ambiguity.related} == {"contains"}
    assert (
        results[1].ambiguity.n_related == 2 and "CONTAINS_SUBSTRUCTURE" in results[1].reason_codes
    )
    assert "WITHIN_LARGER_OBJECT" in results[2].reason_codes
    assert results[1].support_level == "strong"


def test_severe_ambiguity_abstains() -> None:
    crowd = [obj(k, 100 + 0.2 * k, 100) for k in range(1, 5)]  # 4 objects within 1 r50
    results = run(crowd, dets=detections([(100.5, 100.0), FAR_DETECTION]))
    assert all(e.support_level == "insufficient" for e in results)
    assert all("ABSTAIN_AMBIGUOUS" in e.reason_codes for e in results)


def test_distinguishable_compact_competitor_is_minor() -> None:
    pair = [obj(1, 100, 100), obj(2, 102.5, 100)]  # 2.5 r50 apart
    results = run(pair, dets=detections([(100.2, 100.0), FAR_DETECTION]))
    assert results[0].ambiguity.severity == "minor"
    assert results[0].support_level == "moderate"


def test_results_are_deterministic_and_order_independent() -> None:
    image = plane(discs=[(200, 150, 20, 30.0)])
    objects = [obj(1, 200, 150, radius_px=20), obj(2, 100, 100), obj(3, 101, 100), obj(4, 50, 250)]
    dets = detections([(100.4, 100.0), (50, 250.5), FAR_DETECTION])
    first = run(objects, image=image, dets=dets)
    again = run(objects, image=image, dets=dets)
    reverse = run(objects[::-1], image=image, dets=dets)
    assert [e.to_dict() for e in first] == [e.to_dict() for e in again]
    assert {e.catalogue_id: e.to_dict() for e in first} == {
        e.catalogue_id: e.to_dict() for e in reverse
    }


def test_missing_values_stay_missing() -> None:
    e = run([obj(1, 100, 100)], dets=None)[0]
    assert e.image_grade == "unavailable" and e.support_level == "catalogue-only"
    assert "detection_catalogue" in e.missing and e.compact.separation_px is None
    assert e.evidence_score is None


def test_config_validation_and_helpers() -> None:
    with pytest.raises(ConfigurationError):
        EvidenceConfig(close_offset=4.0)  # must not exceed consistent_offset
    with pytest.raises(ConfigurationError):
        EvidenceConfig(contrast_weak=10.0)
    assert (
        weaker("strong", "weak") == "weak"
        and weaker("catalogue-only", "moderate") == "catalogue-only"
    )

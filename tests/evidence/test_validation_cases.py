"""Synthetic validation cases. Expected levels were fixed before any benchmark was run."""

from __future__ import annotations

import pytest

from tests.evidence.fixtures import FAR_DETECTION, detections, field, level, obj, plane, run

FAR = [FAR_DETECTION]  # one unrelated detection far from everything (low density)


def test_perfect_compact_identification_is_strong() -> None:
    e = run([obj(1, 100, 100)], dets=detections([(100.3, 100.0), *FAR]))[0]
    assert e.support_level == "strong"
    assert {"POSITION_MATCH_CLOSE", "ASTROMETRY_PRECISE"} <= set(e.reason_codes)
    assert e.compact.normalized_offset == pytest.approx(0.3)


def test_close_but_not_exact_compact_identification_is_moderate() -> None:
    e = run([obj(1, 100, 100)], dets=detections([(102.2, 100.0), *FAR]))[0]  # 2.2 r50
    assert e.support_level == "moderate" and "POSITION_MATCH_CONSISTENT" in e.reason_codes


def test_poor_compact_association_is_weak() -> None:
    e = run([obj(1, 100, 100)], dets=detections([(104.0, 100.0), *FAR]))[0]  # 4 r50
    assert e.support_level == "weak" and "POSITION_MATCH_POOR" in e.reason_codes


def test_catalogue_only_compact_object() -> None:
    e = run([obj(1, 100, 100)], dets=detections(FAR))[0]
    assert e.support_level == "catalogue-only" and "NO_IMAGE_ASSOCIATION" in e.reason_codes
    assert e.image_grade == "none"  # measured absence, not contradiction


def test_strong_extended_object() -> None:
    image = plane(discs=[(200, 150, 20, 30.0)])
    e = run([obj(1, 200, 150, radius_px=20)], image=image, dets=[])[0]
    assert e.support_level == "strong"
    assert {"EXTENDED_CONTRAST_STRONG", "STRUCTURE_CENTRED", "EXTENT_MOSTLY_VISIBLE"} <= set(
        e.reason_codes
    )
    assert e.compact is None and e.extended.contrast > 10


def test_weak_extended_object() -> None:
    image = plane(discs=[(200, 150, 20, 2.0)])  # 1 sigma per pixel, many pixels
    e = run([obj(1, 200, 150, radius_px=20)], image=image, dets=[])[0]
    assert e.support_level == "weak" and "EXTENDED_CONTRAST_WEAK" in e.reason_codes


def test_truncated_extended_object_is_capped() -> None:
    image = plane(discs=[(410, 150, 20, 30.0)])  # centre 10 px beyond the right edge
    e = run([obj(1, 410, 150, radius_px=20, centre_in=False)], image=image, dets=[])[0]
    assert e.extended.visibility == "truncated"
    assert e.support_level == "moderate" and "OBJECT_TRUNCATED" in e.caps


def test_extended_object_mostly_outside_abstains() -> None:
    record = obj(1, 420, 150, radius_px=25, centre_in=False)  # 5 px of it inside the frame
    e = run([record], dets=[])[0]
    assert e.support_level == "insufficient" and "ABSTAIN_MOSTLY_OUTSIDE" in e.reason_codes


def test_ambiguous_compact_pair_is_weak_for_both() -> None:
    pair = [obj(1, 100, 100), obj(2, 101, 100)]  # 1 r50 apart: indistinguishable
    results = run(pair, dets=detections([(100.5, 100.0), *FAR]))
    assert [e.support_level for e in results] == ["weak", "weak"]
    assert all(e.ambiguity.severity == "major" for e in results)
    assert results[0].ambiguity.competitors[0].catalogue_id == 2  # recorded, no winner


def test_overlapping_extended_footprints_are_ambiguous() -> None:
    image = plane(discs=[(200, 150, 20, 30.0)])
    pair = [obj(1, 200, 150, radius_px=20), obj(2, 203, 150, radius_px=22)]
    results = run(pair, image=image, dets=[])
    assert all(e.ambiguity.severity == "major" for e in results)
    assert all(e.support_level == "weak" for e in results)


def test_missing_angular_extent_uses_compact_path_without_penalty() -> None:
    e = run([obj(1, 100, 100, radius_px=None)], dets=detections([(100.3, 100), *FAR]))[0]
    assert e.path == "compact" and "MISSING_SIZE" in e.reason_codes
    assert "angular_size" in e.missing and e.support_level == "strong"


def test_missing_epoch_is_reported_not_penalised() -> None:
    dets = detections([(100.3, 100.0), *FAR])
    without = run([obj(1, 100, 100)], dets=dets, astrometry=field(epoch=False))[0]
    with_epoch = run([obj(1, 100, 100)], dets=dets, astrometry=field(epoch=True))[0]
    assert (
        "MISSING_EPOCH" in without.reason_codes and "MISSING_EPOCH" not in with_epoch.reason_codes
    )
    assert without.support_level == with_epoch.support_level == "strong"


def test_saturated_compact_source() -> None:
    calibrated = detections(
        [(100.3, 100.0), *FAR], saturated=True, astrometric_method="isophote_calibrated"
    )
    fallback = detections(
        [(100.3, 100.0), *FAR], saturated=True, astrometric_method="saturated_core_fallback"
    )
    assert level([obj(1, 100, 100)], dets=calibrated) == "strong"
    e = run([obj(1, 100, 100)], dets=fallback)[0]
    assert e.support_level == "moderate" and "SOURCE_SATURATED_UNCALIBRATED" in e.caps


def test_bad_wcs() -> None:
    bad = field(grade="poor", r50=6.0, n=4)
    crowded = detections(
        [(100 + 2.0, 100.0)] + [(20 + 37 * i, 20 + 26 * j) for i in range(10) for j in range(10)]
    )  # 101 detections
    e = run([obj(1, 100, 100)], dets=crowded, astrometry=bad)[0]
    assert e.support_level == "weak"  # positional match does not discriminate, WCS poor
    assert {"ASTROMETRY_POOR", "POSITION_NOT_DISCRIMINATING"} <= set(e.reason_codes)
    nothing = run([obj(1, 100, 100)], dets=detections(FAR), astrometry=bad)[0]
    assert nothing.support_level == "insufficient" and "ABSTAIN_NO_SUPPORT" in nothing.reason_codes


def test_unavailable_astrometry_abstains() -> None:
    e = run(
        [obj(1, 100, 100)],
        dets=detections([(100.3, 100)]),
        astrometry=field(grade="unavailable", r50=None, n=None),
    )[0]
    assert e.support_level == "insufficient" and "ABSTAIN_NO_ASTROMETRY" in e.reason_codes


def test_candidate_type_caps_at_moderate() -> None:
    e = run([obj(1, 100, 100, candidate=True)], dets=detections([(100.3, 100), *FAR]))[0]
    assert e.support_level == "moderate" and "CANDIDATE_TYPE" in e.caps

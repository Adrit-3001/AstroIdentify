from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from astroidentify.config import DetectionConfig
from astroidentify.detection.filtering import (
    DUPLICATE_SATURATED,
    ELONGATED_SATURATED_REGION,
    EXTENDED,
    LOW_SNR,
    NOT_STAR_LIKE,
    REJECTION_REASONS,
    TOO_CLOSE_TO_EDGE,
    TOO_ELONGATED,
    apply_filters,
    rejection_reasons,
)
from astroidentify.detection.types import Source
from astroidentify.exceptions import ConfigurationError

FWHM = 4.0
CONFIG = DetectionConfig()


def _source(**changes) -> Source:
    base = Source(
        source_id=1,
        x=50.0,
        y=50.0,
        flux=1000.0,
        flux_err=20.0,
        snr=50.0,
        peak=100.0,
        fwhm=4.0,
        sharpness=0.5,
        roundness1=0.1,
        roundness2=-0.1,
        local_background=10.0,
        local_rms=2.0,
        edge_distance=50.0,
        n_saturated_pixels=0,
        saturated=False,
        edge=False,
    )
    return dataclasses.replace(base, **changes)


def _reasons(source: Source, reference: float | None = 4.0) -> tuple[str, ...]:
    return rejection_reasons(source, FWHM, reference, CONFIG)


def test_good_source_passes() -> None:
    assert _reasons(_source()) == ()


def test_low_snr_and_nan_snr() -> None:
    assert _reasons(_source(snr=4.9)) == (LOW_SNR,)
    assert _reasons(_source(snr=math.nan)) == (LOW_SNR,)


def test_edge_rejection_only_when_core_is_cut() -> None:
    assert _reasons(_source(edge_distance=FWHM * 0.9, edge=True)) == (TOO_CLOSE_TO_EDGE,)
    assert _reasons(_source(edge_distance=FWHM * 1.5, edge=True)) == ()


def test_multiple_reasons_are_all_recorded() -> None:
    reasons = _reasons(_source(snr=2.0, edge_distance=1.0))
    assert set(reasons) == {LOW_SNR, TOO_CLOSE_TO_EDGE}


def test_shape_rules_for_unsaturated_sources() -> None:
    assert _reasons(_source(sharpness=0.1)) == (NOT_STAR_LIKE,)
    assert _reasons(_source(sharpness=1.2)) == (NOT_STAR_LIKE,)
    assert _reasons(_source(roundness2=1.3)) == (TOO_ELONGATED,)
    assert _reasons(_source(fwhm=4.0 * 2.1)) == (EXTENDED,)
    assert _reasons(_source(fwhm=4.0 * 2.1), reference=None) == ()  # no reference, no rule


def test_saturation_alone_never_rejects() -> None:
    saturated = _source(saturated=True, n_saturated_pixels=40, sharpness=0.1, roundness1=1.5)
    saturated = dataclasses.replace(saturated, fwhm=20.0)
    assert _reasons(saturated) == ()


def test_elongated_saturated_region_only_for_large_regions() -> None:
    arc = _source(saturated=True, saturated_core_axis_ratio=0.3, saturated_core_area=400)
    assert _reasons(arc) == (ELONGATED_SATURATED_REGION,)
    tiny = _source(saturated=True, saturated_core_axis_ratio=0.3, saturated_core_area=4)
    assert _reasons(tiny) == ()
    round_core = _source(saturated=True, saturated_core_axis_ratio=0.8, saturated_core_area=400)
    assert _reasons(round_core) == ()


def test_duplicate_of_saturated_source() -> None:
    assert _reasons(_source(duplicate_of=3, saturated=True)) == (DUPLICATE_SATURATED,)


def test_missing_daofind_statistics_skip_shape_rules() -> None:
    peak_only = _source(
        sharpness=math.nan, roundness1=math.nan, roundness2=math.nan, centroid_method="peak_com"
    )
    assert _reasons(peak_only) == ()


def test_apply_filters_keeps_order_and_computes_reference() -> None:
    sources = [_source(source_id=i, fwhm=4.0 + 0.01 * i) for i in range(1, 8)]
    sources.append(_source(source_id=8, fwhm=12.0))
    filtered, reference = apply_filters(sources, FWHM, CONFIG)
    assert [s.source_id for s in filtered] == list(range(1, 9))
    assert reference == pytest.approx(np.median([s.fwhm for s in sources]))
    assert filtered[-1].rejection_reasons == (EXTENDED,)
    assert all(s.accepted for s in filtered[:-1])


def test_every_reason_is_documented() -> None:
    for reason in (
        LOW_SNR,
        TOO_CLOSE_TO_EDGE,
        NOT_STAR_LIKE,
        TOO_ELONGATED,
        EXTENDED,
        DUPLICATE_SATURATED,
        ELONGATED_SATURATED_REGION,
    ):
        assert reason in REJECTION_REASONS


@pytest.mark.parametrize(
    "changes",
    [
        {"detection_sigma": 0.0},
        {"fwhm": -1.0},
        {"background_filter_size": 2},
        {"sharpness_range": (1.0, 0.2)},
        {"min_saturated_axis_ratio": 1.5},
        {"fwhm_initial_guess": 50.0},
    ],
)
def test_invalid_detection_config(changes: dict) -> None:
    with pytest.raises(ConfigurationError):
        DetectionConfig(**changes)

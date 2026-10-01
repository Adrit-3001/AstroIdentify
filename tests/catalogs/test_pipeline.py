from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest
from astropy.wcs import WCS

from astroidentify.catalogs.pipeline import load_match_inputs, match_catalog
from astroidentify.catalogs.types import CatalogTable
from astroidentify.config import CatalogConfig
from astroidentify.exceptions import CatalogError, InputMismatchError, InvalidPlateSolutionError
from tests.astrometry.fixtures import make_truth_wcs
from tests.catalogs.fixtures import CENTRE, SCALE, FakeProvider, H, W, sources_at, synthetic_field


def _shifted(wcs: WCS, arcsec: float) -> WCS:
    """A WCS whose reference point is off by ``arcsec`` (like the benchmark plate solution)."""
    shifted = wcs.deepcopy()
    shifted.wcs.crval = [
        wcs.wcs.crval[0] + arcsec / 3600 / np.cos(np.radians(CENTRE[1])),
        wcs.wcs.crval[1] + arcsec / 3600,
    ]
    return shifted


def _noisy_sources(field, sigma_px=0.3, seed=2):
    rng = np.random.default_rng(seed)
    return sources_at(field.star_xy + rng.normal(0, sigma_px, field.star_xy.shape))


def test_end_to_end_with_exact_wcs() -> None:
    field = synthetic_field()
    provider = FakeProvider(field.rows)
    result = match_catalog(_noisy_sources(field), field.wcs, W, H, provider, CatalogConfig())
    s = result.summary
    assert s.detections_considered == 60 and s.matches == 60
    assert s.median_residual_arcsec < 0.6 and s.max_residual_arcsec < 3.0
    assert s.unmatched_detections == 0
    assert s.rows_eligible == min(s.rows_in_image, 180)  # brightest 3 x 60
    # The query cone came from the WCS.
    assert provider.regions[0].centre.ra_deg == pytest.approx(CENTRE[0])
    # Every match carries both IDs and is unique on both sides.
    assert len({m.detection_source_id for m in result.matches}) == 60
    assert len({m.gaia_source_id for m in result.matches}) == 60


def test_refinement_recovers_a_systematically_offset_wcs() -> None:
    field = synthetic_field()
    biased = _shifted(field.wcs, 4.0)  # 4": larger than the 3" match radius
    sources = _noisy_sources(field)
    refined = match_catalog(sources, biased, W, H, FakeProvider(field.rows), CatalogConfig())
    raw = match_catalog(
        sources, biased, W, H, FakeProvider(field.rows), CatalogConfig(refine_wcs=False)
    )
    assert refined.wcs_refined and refined.refinement.input_median_offset_arcsec == pytest.approx(
        4.0 * np.sqrt(2), rel=0.1
    )
    assert refined.summary.matches == 60 and refined.summary.median_residual_arcsec < 0.6
    assert raw.summary.matches < 10  # without refinement the offset defeats the radius


def test_saturated_detections_are_matched_but_not_used_for_refinement() -> None:
    field = synthetic_field()
    sources = _noisy_sources(field)
    sources = [dataclasses.replace(s, saturated=i < 10) for i, s in enumerate(sources)]
    result = match_catalog(
        sources, _shifted(field.wcs, 4.0), W, H, FakeProvider(field.rows), CatalogConfig()
    )
    assert result.refinement.n_pairs <= 50
    assert sum(m.detection_saturated for m in result.matches) == 10


def test_all_accepted_detections_are_used_and_rejected_ignored() -> None:
    field = synthetic_field()
    sources = _noisy_sources(field)
    sources[0] = dataclasses.replace(sources[0], accepted=False, rejection_reasons=("low_snr",))
    result = match_catalog(sources, field.wcs, W, H, FakeProvider(field.rows), CatalogConfig())
    assert result.summary.detections_considered == 59
    assert sources[0].source_id not in {m.detection_source_id for m in result.matches}


def test_unmatched_detections_and_deterministic_output() -> None:
    field = synthetic_field()
    extra = sources_at(np.array([[200.0, 150.0], [30.0, 280.0]]), start_id=500)
    far = [s for s in extra if True]
    sources = _noisy_sources(field) + far
    a = match_catalog(sources, field.wcs, W, H, FakeProvider(field.rows), CatalogConfig())
    b = match_catalog(
        list(reversed(sources)), field.wcs, W, H, FakeProvider(field.rows), CatalogConfig()
    )
    assert [m.to_dict() for m in a.matches] == [m.to_dict() for m in b.matches]
    assert [m.detection_source_id for m in a.matches] == sorted(
        m.detection_source_id for m in a.matches
    )
    assert a.summary.unmatched_detections >= 1
    assert set(a.unmatched_detection_ids) <= {500, 501}


def test_no_catalogue_rows_is_a_valid_empty_result() -> None:
    field = synthetic_field()
    empty = CatalogTable({k: v[:0] for k, v in field.rows.columns.items()})
    result = match_catalog(
        _noisy_sources(field), field.wcs, W, H, FakeProvider(empty), CatalogConfig()
    )
    assert result.summary.matches == 0 and result.summary.rows_returned == 0
    assert any("no rows" in w for w in result.warnings)
    assert any("no detection matched" in w for w in result.warnings)


def test_no_accepted_detections_is_an_error() -> None:
    field = synthetic_field()
    sources = sources_at(field.star_xy, accepted=False)
    with pytest.raises(CatalogError, match="no accepted detections"):
        match_catalog(sources, field.wcs, W, H, FakeProvider(field.rows), CatalogConfig())


def test_invalid_wcs_is_rejected() -> None:
    field = synthetic_field()
    bad = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=SCALE)
    bad.wcs.cd = [[0.0, 0.0], [0.0, 0.0]]
    with pytest.raises(InvalidPlateSolutionError):
        match_catalog(_noisy_sources(field), bad, W, H, FakeProvider(field.rows), CatalogConfig())


def test_load_match_inputs_checks_consistency(saved_products) -> None:
    paths = saved_products
    inputs = load_match_inputs(paths["image"], paths["plate"], paths["wcs"], paths["sources"])
    assert inputs.width == 200 and inputs.height == 160 and len(inputs.sources) > 0
    plate = json.loads(paths["plate"].read_text())

    plate["solved"] = False
    paths["plate"].write_text(json.dumps(plate))
    with pytest.raises(InvalidPlateSolutionError, match="not solved"):
        load_match_inputs(paths["image"], paths["plate"], paths["wcs"], paths["sources"])

    plate["solved"] = True
    plate["input"]["source_sha256"] = "0" * 64
    paths["plate"].write_text(json.dumps(plate))
    with pytest.raises(InputMismatchError, match="different image"):
        load_match_inputs(paths["image"], paths["plate"], paths["wcs"], paths["sources"])

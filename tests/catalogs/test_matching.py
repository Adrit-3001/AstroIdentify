from __future__ import annotations

import numpy as np
import pytest
from astropy.coordinates import SkyCoord

from astroidentify.catalogs.matching import (
    Edge,
    assign_one_to_one,
    candidate_edges,
    eligible_catalog_indices,
    fit_refined_wcs,
    registration_pairs,
)
from tests.astrometry.fixtures import make_truth_wcs
from tests.catalogs.fixtures import CENTRE, H, W

DET_IDS = np.array([101, 102, 103, 104])
CAT_IDS = np.array([9001, 9002, 9003, 9004])


def _edges(*triples):
    return [Edge(d, c, s) for d, c, s in triples]


def test_perfect_matches() -> None:
    accepted = assign_one_to_one(_edges((0, 0, 0.1), (1, 1, 0.2), (2, 2, 0.0)), DET_IDS, CAT_IDS)
    assert {(e.detection_index, e.catalog_index) for e in accepted} == {(0, 0), (1, 1), (2, 2)}


def test_two_detections_compete_for_one_catalog_star() -> None:
    accepted = assign_one_to_one(_edges((0, 0, 1.5), (1, 0, 0.4)), DET_IDS, CAT_IDS)
    assert [(e.detection_index, e.catalog_index) for e in accepted] == [(1, 0)]


def test_two_catalog_stars_compete_for_one_detection() -> None:
    accepted = assign_one_to_one(_edges((0, 0, 2.0), (0, 1, 0.5)), DET_IDS, CAT_IDS)
    assert [(e.detection_index, e.catalog_index) for e in accepted] == [(0, 1)]


def test_conflict_chain_resolves_to_closest_pairs() -> None:
    # d0-c0 0.5, d0-c1 0.3, d1-c1 0.4: d0 takes c1 (0.3); d1 loses c1; d0's c0 edge unused.
    accepted = assign_one_to_one(
        _edges((0, 0, 0.5), (0, 1, 0.3), (1, 1, 0.4), (1, 0, 0.9)), DET_IDS, CAT_IDS
    )
    assert {(e.detection_index, e.catalog_index) for e in accepted} == {(0, 1), (1, 0)}


def test_exact_ties_are_broken_by_ids() -> None:
    # Same separation: the lower Gaia source_id wins, then the lower detection source_id.
    accepted = assign_one_to_one(_edges((1, 1, 0.5), (0, 1, 0.5), (0, 0, 0.5)), DET_IDS, CAT_IDS)
    assert {(e.detection_index, e.catalog_index) for e in accepted} == {(0, 0), (1, 1)}
    accepted = assign_one_to_one(_edges((1, 0, 0.5), (0, 0, 0.5)), DET_IDS, CAT_IDS)
    assert [(e.detection_index, e.catalog_index) for e in accepted] == [(0, 0)]


def test_assignment_is_deterministic_under_input_order() -> None:
    edges = _edges((0, 0, 0.5), (0, 1, 0.3), (1, 1, 0.4), (1, 0, 0.9), (2, 2, 0.1), (3, 2, 0.1))
    reference = assign_one_to_one(edges, DET_IDS, CAT_IDS)
    for seed in range(5):
        shuffled = list(np.random.default_rng(seed).permutation(edges))
        assert assign_one_to_one(shuffled, DET_IDS, CAT_IDS) == reference


def test_one_to_one_uniqueness_on_random_edges() -> None:
    rng = np.random.default_rng(3)
    edges = [
        Edge(int(d), int(c), float(s))
        for d, c, s in zip(
            rng.integers(0, 4, 40), rng.integers(0, 4, 40), rng.uniform(0, 3, 40), strict=True
        )
    ]
    accepted = assign_one_to_one(edges, DET_IDS, CAT_IDS)
    assert len({e.detection_index for e in accepted}) == len(accepted)
    assert len({e.catalog_index for e in accepted}) == len(accepted)


def test_candidate_edges_respect_radius() -> None:
    det = SkyCoord([10.0, 10.0], [20.0, 20.01], unit="deg")
    cat = SkyCoord([10.0 + 1.0 / 3600, 10.0], [20.0, 20.0 + 5.0 / 3600], unit="deg")
    edges = candidate_edges(det, cat, radius_arcsec=3.0)
    assert len(edges) == 1 and edges[0].detection_index == 0 and edges[0].catalog_index == 0
    assert edges[0].separation_arcsec == pytest.approx(1.0 * np.cos(np.radians(20)), rel=1e-3)
    assert candidate_edges(det, cat[:0], 3.0) == []


def test_eligibility_keeps_the_brightest_in_image() -> None:
    mags = np.array([15.0, 9.0, np.nan, 12.0, 11.0, 8.0])
    in_image = np.array([True, True, True, True, True, False])
    assert list(eligible_catalog_indices(mags, in_image, 2, factor=1.0)) == [1, 4]
    assert list(eligible_catalog_indices(mags, in_image, 2, factor=None)) == [0, 1, 2, 3, 4]
    assert list(eligible_catalog_indices(mags, in_image, 10, factor=1.0)) == [0, 1, 2, 3, 4]


def test_registration_pairs_require_mutual_nearest_and_isolation() -> None:
    det = np.array([[10.0, 10.0], [50.0, 50.0], [90.0, 90.0]])
    cat = np.array([[11.0, 10.0], [52.0, 50.0], [53.5, 50.0], [200.0, 200.0]])
    pairs = registration_pairs(det, cat, radius_px=5.0, isolation_ratio=2.0)
    assert pairs == [(0, 0)]  # detection 1 is ambiguous (two stars), detection 2 too far


def test_refined_wcs_recovers_truth_with_canonical_pixels() -> None:
    """Regression for Astropy's fitter origin: canonical (0-based) pixels must round-trip."""
    truth = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=1.0, rotation_deg=25.0)
    rng = np.random.default_rng(0)
    xy = np.column_stack([rng.uniform(0, W - 1, 80), rng.uniform(0, H - 1, 80)])
    world = SkyCoord(*truth.all_pix2world(xy[:, 0], xy[:, 1], 0), unit="deg")
    wcs, used, reason = fit_refined_wcs(xy, world, None, 3.0, 20)
    assert reason == "refined" and used.all()
    ra, dec = wcs.all_pix2world(xy[:, 0], xy[:, 1], 0)
    assert SkyCoord(ra, dec, unit="deg").separation(world).arcsec.max() < 1e-3


def test_refinement_clips_outlier_pairs() -> None:
    truth = make_truth_wcs(W, H, centre=CENTRE, scale_arcsec=1.0)
    rng = np.random.default_rng(1)
    xy = np.column_stack([rng.uniform(0, W - 1, 60), rng.uniform(0, H - 1, 60)])
    noisy = xy + rng.normal(0, 0.2, xy.shape)
    noisy[:3] += 15.0  # three wrong pairs
    world = SkyCoord(*truth.all_pix2world(xy[:, 0], xy[:, 1], 0), unit="deg")
    _, used, _ = fit_refined_wcs(noisy, world, None, 4.0, 20)  # the default clip level
    assert not used[:3].any() and used[3:].all()


def test_too_few_pairs_is_reported() -> None:
    world = SkyCoord([10.0] * 5, [20.0] * 5, unit="deg")
    wcs, _, reason = fit_refined_wcs(np.zeros((5, 2)), world, None, 3.0, 20)
    assert wcs is None and "only 5" in reason

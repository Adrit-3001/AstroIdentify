from __future__ import annotations

import dataclasses

import pytest

from astroidentify.astrometry.selection import (
    grid_shape_for,
    select_from_sources,
    source_tier,
)
from astroidentify.astrometry.types import (
    TIER_EDGE,
    TIER_PREFERRED,
    TIER_SATURATED,
    TIER_SECONDARY,
)
from astroidentify.exceptions import NoUsableSourcesError, TooFewSourcesError
from tests.detection.test_filtering import _source

W, H = 400, 300
ALL = (TIER_PREFERRED, TIER_SECONDARY, TIER_SATURATED, TIER_EDGE)
UNSATURATED = (TIER_PREFERRED, TIER_SECONDARY)


def _src(source_id: int, x: float, y: float, flux: float, **changes):
    return dataclasses.replace(
        _source(), source_id=source_id, x=x, y=y, flux=flux, snr=flux / 10, **changes
    )


def _select(sources, *, max_sources=10, min_sources=3, tiers=ALL, grid=(2, 2)):
    return select_from_sources(
        sources,
        width=W,
        height=H,
        max_sources=max_sources,
        min_sources=min_sources,
        allowed_tiers=tiers,
        grid_shape=grid,
    )


def test_tiers() -> None:
    assert source_tier(_src(1, 50, 50, 10)) == TIER_PREFERRED
    assert source_tier(_src(1, 50, 50, 10, centroid_method="peak_com")) == TIER_SECONDARY
    assert source_tier(_src(1, 50, 50, 10, saturated=True)) == TIER_SATURATED
    assert source_tier(_src(1, 50, 50, 10, edge=True, saturated=True)) == TIER_EDGE


def test_only_accepted_sources_are_used() -> None:
    sources = [_src(i, 20 * i, 20 * i, 100 - i) for i in range(1, 8)]
    sources[0] = dataclasses.replace(sources[0], accepted=False, rejection_reasons=("low_snr",))
    selection = _select(sources)
    assert 1 not in {s.source_id for s in selection.sources}
    assert len(selection) == 6


def test_unsaturated_preferred_over_brighter_saturated() -> None:
    bright_saturated = [_src(i, 30 + 40 * i, 50, 1e6, saturated=True) for i in range(5)]
    faint = [_src(10 + i, 30 + 40 * i, 200, 100.0 - i) for i in range(5)]
    selection = _select(bright_saturated + faint, max_sources=5)
    assert all(not s.saturated for s in selection.sources)


def test_saturated_used_to_fill_when_allowed() -> None:
    saturated = [_src(i, 30 + 40 * i, 50, 1e6, saturated=True) for i in range(5)]
    faint = [_src(10 + i, 30 + 40 * i, 200, 100.0 - i) for i in range(3)]
    selection = _select(saturated + faint, max_sources=6)
    assert selection.n_by_tier == {TIER_PREFERRED: 3, TIER_SATURATED: 3}
    # Ranks follow flux, so the (brighter) saturated stars come first in the solver list.
    assert [s.saturated for s in selection.sources][:3] == [True, True, True]


def test_edge_sources_are_last_resort() -> None:
    edge = [_src(i, 2, 30 * i + 10, 1e5, edge=True) for i in range(1, 6)]
    inner = [_src(10 + i, 100 + 30 * i, 150, 50.0) for i in range(4)]
    assert all(not s.edge for s in _select(edge + inner, max_sources=4).sources)
    assert sum(s.edge for s in _select(edge + inner, max_sources=6).sources) == 2
    assert all(not s.edge for s in _select(edge + inner, max_sources=9, tiers=UNSATURATED).sources)


def test_spatial_balancing_across_cells() -> None:
    # 20 very bright sources crowded in the top-left cell, 3 fainter ones in each other cell.
    crowded = [_src(i, 10 + 8 * (i % 5), 10 + 8 * (i // 5), 1e4 - i) for i in range(20)]
    others = [
        _src(100 + 10 * c + k, cx + 10 * k, cy, 50.0 - k)
        for c, (cx, cy) in enumerate([(300, 50), (50, 250), (300, 250)])
        for k in range(3)
    ]
    selection = _select(crowded + others, max_sources=8)
    per_cell: dict[tuple[int, int], int] = {}
    for s in selection.sources:
        per_cell[s.cell] = per_cell.get(s.cell, 0) + 1
    assert selection.occupied_cells == 4
    assert per_cell == {(0, 0): 2, (1, 0): 2, (0, 1): 2, (1, 1): 2}
    # A plain brightest-N would have taken 8 sources from the crowded cell.


def test_brightness_within_cells_and_rank_order() -> None:
    sources = [_src(i, 20 + (i % 10) * 35, 20 + (i // 10) * 120, float(i)) for i in range(1, 31)]
    selection = _select(sources, max_sources=12)
    fluxes = [s.flux for s in selection.sources]
    assert fluxes == sorted(fluxes, reverse=True)
    assert [s.rank for s in selection.sources] == list(range(1, 13))
    # Each cell contributes its brightest sources first.
    for cell in {s.cell for s in selection.sources}:
        chosen = {s.source_id for s in selection.sources if s.cell == cell}
        in_cell = [
            s
            for s in sources
            if (min(1, int((s.x + 0.5) * 2 / W)), min(1, int((s.y + 0.5) * 2 / H))) == cell
        ]
        best = sorted(in_cell, key=lambda s: -s.flux)[: len(chosen)]
        assert chosen == {s.source_id for s in best}


def test_selection_is_deterministic_and_canonical_coordinates_unchanged() -> None:
    sources = [_src(i, 13.37 * i % W, 7.11 * i % H, 1000.0 / i) for i in range(1, 60)]
    first, second = (
        _select(sources, max_sources=20),
        _select(list(reversed(sources)), max_sources=20),
    )
    assert first.sources == second.sources
    by_id = {s.source_id: s for s in sources}
    for chosen in first.sources:
        assert (chosen.x, chosen.y) == (by_id[chosen.source_id].x, by_id[chosen.source_id].y)


def test_too_few_and_no_sources() -> None:
    with pytest.raises(TooFewSourcesError, match="at least 5"):
        _select([_src(i, 50 * i, 50, 10.0) for i in range(1, 4)], min_sources=5)
    rejected = [dataclasses.replace(_src(1, 5, 5, 1.0), accepted=False)]
    with pytest.raises(NoUsableSourcesError):
        _select(rejected)
    with pytest.raises(NoUsableSourcesError, match="allowed tiers"):
        _select([_src(1, 5, 5, 1.0, saturated=True)], tiers=UNSATURATED, min_sources=1)


def test_grid_shape_follows_aspect_ratio() -> None:
    assert grid_shape_for(2560, 1920, 16) == (5, 3)
    assert grid_shape_for(1000, 1000, 16) == (4, 4)
    assert grid_shape_for(100, 1000, 16) == (1, 16)


def test_pooled_mode_ranks_saturated_with_unsaturated_by_brightness() -> None:
    """Regression: sequential tiers never sent the bright saturated stars to the solver."""
    # One bright saturated star per grid cell (plus one extra), many fainter unsaturated ones.
    positions = [(50, 50), (300, 50), (50, 250), (300, 250), (120, 60)]
    saturated = [_src(i, x, y, 1e6 - i, saturated=True) for i, (x, y) in enumerate(positions)]
    faint = [_src(10 + i, 20 + 18 * i, 100 + 10 * (i % 10), 100.0 - i) for i in range(20)]
    tiers = (TIER_PREFERRED, TIER_SECONDARY, TIER_SATURATED)

    sequential = _select(saturated + faint, max_sources=10, tiers=tiers)
    assert not any(s.saturated for s in sequential.sources)  # old behaviour
    pooled = select_from_sources(
        saturated + faint, width=W, height=H, max_sources=10, min_sources=3,
        allowed_tiers=tiers, grid_shape=(2, 2), pooled=True,
    )  # fmt: skip
    assert pooled.tier_mode == "pooled"
    assert [s.source_id for s in pooled.sources[:5]] == [0, 1, 2, 3, 4]
    assert all(s.tier == TIER_SATURATED for s in pooled.sources[:5])

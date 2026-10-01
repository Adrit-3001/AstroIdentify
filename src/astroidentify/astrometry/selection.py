"""Deterministic, quality-aware, spatially balanced source selection for plate solving.

Strategy:

1. **Eligibility.** Only accepted Milestone 2 sources are used.
2. **Quality tiers**: ``preferred`` (unsaturated, not edge-flagged, DAOFIND centroid),
   ``secondary`` (unsaturated, not edge-flagged, peak-search centroid), ``saturated``
   (saturated-core centroid, not edge-flagged) and ``edge`` (edge-flagged). Each attempt
   allows a subset of tiers, combined in one of two modes:

   * ``pooled``: all allowed tiers compete together on brightness. This matches how blind
     solvers work. Astrometry.net index files hold the *brightest* stars of each sky region,
     and in typical consumer/processed images those stars are saturated. On the Unistellar
     benchmark the field's 25 index stars were all among the 37 brightest detections and all
     saturated: an unsaturated-only list shared almost none of them and timed out, while
     the brightness-pooled list solved blind in seconds.
   * ``sequential``: better tiers are used up first (e.g. an unsaturated-only attempt for
     images whose saturated centroids are unreliable).
3. **Position.** Every selected position is the Milestone 2 *astrometric* centroid
   (``Source.astrometric_x/y``), which corrects the asymmetric-PSF bias of saturated cores.
4. **Brightness.** Within a tier, sources are ranked by flux (then SNR, then position, for
   determinism). Blind solvers match the brightest field stars to the brightest catalogue
   stars, so brightness matters more than SNR once a source is clearly real.
5. **Spatial balancing.** The image is divided into a grid whose shape follows the aspect
   ratio. Sources are taken round-robin, the brightest remaining in each cell per round, so
   the set cannot collapse onto the brightest or densest region of the frame.

The resulting set is written to the solver sorted by decreasing flux (``rank`` 1 =
brightest); balancing decides *which* sources are sent, not their order.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from astroidentify.astrometry.types import (
    TIER_EDGE,
    TIER_PREFERRED,
    TIER_SATURATED,
    TIER_SECONDARY,
    SelectedSource,
    SourceSelection,
)
from astroidentify.detection.types import DetectionResult, Source
from astroidentify.exceptions import NoUsableSourcesError, TooFewSourcesError

TIER_ORDER: tuple[str, ...] = (TIER_PREFERRED, TIER_SECONDARY, TIER_SATURATED, TIER_EDGE)


def source_tier(source: Source) -> str:
    """Quality tier of an accepted source."""
    if source.edge:
        return TIER_EDGE
    if source.saturated:
        return TIER_SATURATED
    if source.centroid_method == "daofind":
        return TIER_PREFERRED
    return TIER_SECONDARY


def grid_shape_for(width: int, height: int, cells: int) -> tuple[int, int]:
    """``(columns, rows)`` with about ``cells`` roughly square cells for this aspect ratio."""
    columns = max(1, round(math.sqrt(cells * width / height)))
    rows = max(1, round(cells / columns))
    return columns, rows


def select_plate_sources(
    detection: DetectionResult,
    *,
    max_sources: int,
    min_sources: int,
    allowed_tiers: Sequence[str],
    grid_shape: tuple[int, int] | None = None,
    grid_cells: int = 16,
    pooled: bool = False,
) -> SourceSelection:
    """Select up to ``max_sources`` accepted sources from the allowed tiers.

    ``pooled=True`` ranks all allowed tiers together by brightness; otherwise tiers are
    filled in quality order (see the module docstring).

    Raises:
        NoUsableSourcesError: No accepted source is in the allowed tiers.
        TooFewSourcesError: Fewer than ``min_sources`` are available.
    """
    height, width = detection.plane.shape
    return select_from_sources(
        detection.sources,
        width=width,
        height=height,
        max_sources=max_sources,
        min_sources=min_sources,
        allowed_tiers=allowed_tiers,
        grid_shape=grid_shape,
        grid_cells=grid_cells,
        pooled=pooled,
    )


def select_from_sources(
    sources: Iterable[Source],
    *,
    width: int,
    height: int,
    max_sources: int,
    min_sources: int,
    allowed_tiers: Sequence[str],
    grid_shape: tuple[int, int] | None = None,
    grid_cells: int = 16,
    pooled: bool = False,
) -> SourceSelection:
    """Core of :func:`select_plate_sources`, operating on a plain source sequence."""
    accepted = [s for s in sources if s.accepted]
    if not accepted:
        raise NoUsableSourcesError("no accepted sources to plate-solve with")
    tiers = tuple(t for t in TIER_ORDER if t in allowed_tiers)
    candidates_by_tier = {
        tier: [s for s in accepted if source_tier(s) == tier] for tier in TIER_ORDER
    }
    pool = sum((len(candidates_by_tier[t]) for t in tiers), 0)
    if pool == 0:
        raise NoUsableSourcesError(f"no accepted sources in the allowed tiers {list(tiers)}")
    if pool < min_sources:
        raise TooFewSourcesError(
            f"only {pool} usable sources in tiers {list(tiers)}; at least {min_sources} "
            "are needed to plate-solve"
        )

    shape = grid_shape or grid_shape_for(width, height, grid_cells)
    chosen: list[tuple[Source, str]] = []
    if pooled:
        pool_sources = [s for t in tiers for s in candidates_by_tier[t]]
        picked = _balanced(pool_sources, max_sources, width, height, shape)
        chosen.extend((source, source_tier(source)) for source in picked)
    else:
        for tier in tiers:
            remaining = max_sources - len(chosen)
            if remaining <= 0:
                break
            picked = _balanced(candidates_by_tier[tier], remaining, width, height, shape)
            chosen.extend((source, tier) for source in picked)

    chosen.sort(key=lambda item: _brightness_key(item[0]))
    selected = tuple(
        SelectedSource(
            rank=rank,
            source_id=source.source_id,
            x=source.astrometric_xy[0],
            y=source.astrometric_xy[1],
            flux=source.flux,
            snr=source.snr,
            saturated=source.saturated,
            edge=source.edge,
            tier=tier,
            cell=_cell(source, width, height, shape),
            astrometric_method=source.astrometric_method,
        )
        for rank, (source, tier) in enumerate(chosen, start=1)
    )

    warnings: list[str] = []
    cells_with_candidates = {
        _cell(s, width, height, shape) for t in tiers for s in candidates_by_tier[t]
    }
    occupied = {s.cell for s in selected}
    if len(occupied) < len(cells_with_candidates):
        warnings.append(
            f"selection covers {len(occupied)} of {len(cells_with_candidates)} grid cells "
            "that contain usable sources"
        )
    return SourceSelection(
        sources=selected,
        image_width=width,
        image_height=height,
        grid_shape=shape,
        allowed_tiers=tiers,
        max_sources=max_sources,
        n_candidates_by_tier={t: len(v) for t, v in candidates_by_tier.items()},
        warnings=tuple(warnings),
        tier_mode="pooled" if pooled else "sequential",
    )


def _balanced(
    sources: list[Source], limit: int, width: int, height: int, shape: tuple[int, int]
) -> list[Source]:
    """Round-robin over grid cells: each round takes the brightest remaining per cell."""
    cells: dict[tuple[int, int], list[Source]] = {}
    for source in sorted(sources, key=_brightness_key):
        cells.setdefault(_cell(source, width, height, shape), []).append(source)
    order = sorted(cells)  # deterministic (column, row) order
    picked: list[Source] = []
    depth = 0
    while len(picked) < limit and any(len(cells[c]) > depth for c in order):
        round_sources = [cells[c][depth] for c in order if len(cells[c]) > depth]
        round_sources.sort(key=_brightness_key)  # brightest first if a round is cut short
        picked.extend(round_sources[: limit - len(picked)])
        depth += 1
    return picked


def _cell(source: Source, width: int, height: int, shape: tuple[int, int]) -> tuple[int, int]:
    columns, rows = shape
    # Pixel centres span -0.5..W-0.5; clamp so edge centroids stay in the grid.
    x, y = source.astrometric_xy
    column = min(columns - 1, max(0, int((x + 0.5) * columns / width)))
    row = min(rows - 1, max(0, int((y + 0.5) * rows / height)))
    return column, row


def _brightness_key(source: Source) -> tuple[float, float, float, float]:
    flux = source.flux if math.isfinite(source.flux) else -math.inf
    snr = source.snr if math.isfinite(source.snr) else -math.inf
    return (-flux, -snr, source.y, source.x)

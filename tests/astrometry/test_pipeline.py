from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astroidentify.astrometry import plan_attempts, plate_solve, save_astrometry_outputs
from astroidentify.astrometry.diagnostics import match_statistics, read_match_file
from astroidentify.astrometry.overlay import CENTRE_COLOR, MATCH_COLOR, TIER_COLORS
from astroidentify.astrometry.pipeline import select_for_plan
from astroidentify.astrometry.types import (
    MODE_BLIND,
    MODE_SCALE_CONSTRAINED,
    TIER_EDGE,
    TIER_PREFERRED,
    TIER_SATURATED,
)
from astroidentify.config import AstrometryConfig
from astroidentify.exceptions import SolverTimeoutError, TooFewSourcesError
from tests.astrometry.fixtures import HEIGHT, TRUTH_CENTRE, WIDTH


def _config(solver: Path, index_dir: Path, **changes) -> AstrometryConfig:
    base = {
        "solve_field_path": str(solver),
        "index_dirs": (str(index_dir),),
        "max_sources": 20,
        "expanded_max_sources": 40,
        "timeout_seconds": 30.0,
    }
    return AstrometryConfig(**(base | changes))


def _strict_json(path: Path):
    def reject(constant: str) -> None:
        raise ValueError(constant)

    return json.loads(path.read_text(), parse_constant=reject)


def test_attempt_plan() -> None:
    plans = plan_attempts(AstrometryConfig())
    assert [p.name for p in plans] == ["bright", "unsaturated", "expanded"]
    assert all(p.mode == MODE_BLIND and p.scale_bounds_arcsec is None for p in plans)
    # Regression: the first attempt pools saturated stars with the others by brightness.
    assert plans[0].pooled and TIER_SATURATED in plans[0].allowed_tiers
    assert TIER_SATURATED not in plans[1].allowed_tiers
    assert TIER_EDGE in plans[2].allowed_tiers and plans[2].max_sources == 200


def test_scale_constrained_attempt_runs_first_when_configured() -> None:
    plans = plan_attempts(AstrometryConfig(scale_low_arcsec=0.5, scale_high_arcsec=2.0))
    assert [p.mode for p in plans] == [MODE_SCALE_CONSTRAINED] + [MODE_BLIND] * 3
    assert plans[0].scale_bounds_arcsec == (0.5, 2.0)


def test_include_saturated_false_keeps_saturated_out() -> None:
    plans = plan_attempts(AstrometryConfig(include_saturated=False))
    assert all(TIER_SATURATED not in p.allowed_tiers for p in plans)


def test_bright_attempt_selects_bright_saturated_stars(saturated_detection) -> None:
    """Regression for the benchmark failure: the brightest stars (saturated) must be sent.

    With the old policy (unsaturated tiers filled first), none of the bright saturated
    stars reached the solver while enough unsaturated stars existed.
    """
    config = AstrometryConfig(max_sources=20, expanded_max_sources=40)
    selection = select_for_plan(saturated_detection, plan_attempts(config)[0], config)
    brightest = sorted(
        (s for s in saturated_detection.sources if s.accepted and not s.edge),
        key=lambda s: -s.flux,
    )[:5]
    assert all(s.saturated for s in brightest)
    chosen = {s.source_id for s in selection.sources}
    assert {s.source_id for s in brightest} <= chosen
    assert selection.tier_mode == "pooled"
    assert [s.source_id for s in selection.sources][:5] == [s.source_id for s in brightest]


def test_blind_solve_end_to_end(detection, fake_solver, index_dir, solver_calls) -> None:
    solution = plate_solve(detection, _config(fake_solver(), index_dir))

    assert solution.solved and solution.status == "solved"
    assert solution.mode == MODE_BLIND and solution.solved_attempt == 1
    assert solution.constraints["position_hint"] is None
    assert solution.backend == "Astrometry.net" and solution.backend_version == "0.93-fake"
    geometry = solution.geometry
    assert geometry.centre.ra_deg == pytest.approx(TRUTH_CENTRE[0], abs=1e-8)
    assert geometry.centre.dec_deg == pytest.approx(TRUTH_CENTRE[1], abs=1e-8)
    assert geometry.pixel_scale_arcsec == pytest.approx(1.5, rel=1e-4)
    assert geometry.parity == "normal"
    assert len(solver_calls()) == 1

    # Correspondences come back in canonical coordinates and map to selected sources.
    selected = {s.source_id: s for s in solution.selection.sources}
    assert len(solution.correspondences) == 12
    for match in solution.correspondences:
        assert match.source_id in selected
        assert (match.field_x, match.field_y) == pytest.approx(
            (selected[match.source_id].x, selected[match.source_id].y), abs=1e-9
        )
        assert match.residual_px == pytest.approx(np.hypot(0.1, 0.05), abs=1e-9)
    stats = solution.match_statistics
    assert stats.n_matched == 12 and stats.solver_log_odds == pytest.approx(123.4)
    assert stats.median_residual_arcsec == pytest.approx(np.hypot(0.1, 0.05) * 1.5, rel=1e-3)
    assert stats.match_fraction == pytest.approx(12 / len(solution.selection))
    assert stats.solver_counts["nmatch"] == 12


def test_unsolved_runs_each_distinct_attempt_once(
    detection, fake_solver, index_dir, solver_calls
) -> None:
    solution = plate_solve(detection, _config(fake_solver("unsolved"), index_dir))
    assert not solution.solved and solution.status == "unsolved"
    assert "no attempt produced a solution" in solution.error
    ran = [a for a in solution.attempts if a.status != "skipped"]
    assert len(solver_calls()) == len(ran)
    assert all(a.status == "unsolved" for a in ran)
    assert "# attempt 1" in solution.solver_log


def test_scale_constrained_attempt_is_first_and_recorded(
    detection, fake_solver, index_dir, solver_calls
) -> None:
    config = _config(
        fake_solver("unsolved"), index_dir, scale_low_arcsec=1.0, scale_high_arcsec=2.0
    )
    solution = plate_solve(detection, config)
    first = solution.attempts[0]
    assert first.mode == MODE_SCALE_CONSTRAINED and first.scale_bounds_arcsec == (1.0, 2.0)
    assert "--scale-low" in solver_calls()[0]
    assert all("--scale-low" not in call for call in solver_calls()[1:])


def test_scale_constrained_solve_sets_mode(detection, fake_solver, index_dir) -> None:
    config = _config(fake_solver(), index_dir, scale_low_arcsec=1.0, scale_high_arcsec=2.0)
    solution = plate_solve(detection, config)
    assert solution.solved and solution.mode == MODE_SCALE_CONSTRAINED
    assert solution.constraints["scale_bounds_arcsec_per_pixel"] == [1.0, 2.0]
    assert solution.constraints["position_hint"] is None


def test_prerequisites_missing_is_reported(detection, tmp_path) -> None:
    config = AstrometryConfig(solve_field_path=str(tmp_path / "missing-solve-field"))
    solution = plate_solve(detection, config)
    assert solution.status == "prerequisites_missing" and not solution.attempts
    assert solution.selection is not None and len(solution.selection) > 0
    with pytest.raises(Exception, match="not found"):
        solution.raise_for_status()


def test_timeout_does_not_stop_the_fallbacks(
    detection, fake_solver, index_dir, solver_calls
) -> None:
    # (4 s: the fake's "solve" mode needs ~1-2 s to import Astropy.)
    config = _config(fake_solver("sleep,solve"), index_dir, timeout_seconds=4.0)
    solution = plate_solve(detection, config)
    assert solution.solved
    ran = [a for a in solution.attempts if a.status != "skipped"]
    assert [a.status for a in ran] == ["timeout", "solved"]
    assert ran[0].runtime_seconds == pytest.approx(4.0)
    assert solution.solved_attempt == ran[1].number
    assert len(solver_calls()) == 2


def test_all_attempts_timing_out(detection, fake_solver, index_dir, solver_calls) -> None:
    solution = plate_solve(detection, _config(fake_solver("sleep"), index_dir, timeout_seconds=1.0))
    assert solution.status == "timeout"
    assert all(a.status in ("timeout", "skipped") for a in solution.attempts)
    assert len(solver_calls()) == sum(a.status == "timeout" for a in solution.attempts) >= 2
    with pytest.raises(SolverTimeoutError):
        solution.raise_for_status()


def test_total_budget_is_enforced(detection, fake_solver, index_dir, solver_calls) -> None:
    config = _config(
        fake_solver("sleep"), index_dir, timeout_seconds=7.0, total_timeout_seconds=8.0
    )
    started = time.monotonic()
    solution = plate_solve(detection, config)
    assert time.monotonic() - started < 15
    statuses = [a.status for a in solution.attempts]
    assert statuses[0] == "timeout" and "budget_exhausted" in statuses
    assert solution.attempts[0].timeout_seconds == pytest.approx(7.0)
    assert len(solver_calls()) <= 2


@pytest.mark.parametrize("mode", ["no_wcs", "bad_wcs"])
def test_invalid_wcs_output(detection, fake_solver, index_dir, mode) -> None:
    solution = plate_solve(detection, _config(fake_solver(mode), index_dir))
    assert solution.status == "invalid_wcs" and not solution.solved


def test_process_failure_continues_and_is_reported(
    detection, fake_solver, index_dir, solver_calls
) -> None:
    solution = plate_solve(detection, _config(fake_solver("fail"), index_dir))
    assert solution.status == "solver_error" and "solver_error" in solution.error
    assert "something broke" in solution.solver_log
    assert len(solver_calls()) >= 2  # later attempts still ran
    assert plate_solve(detection, _config(fake_solver("fail,solve"), index_dir)).solved


def test_unsolved_beats_solver_error_in_overall_status(detection, fake_solver, index_dir) -> None:
    solution = plate_solve(detection, _config(fake_solver("fail,unsolved"), index_dir))
    assert solution.status == "unsolved"
    assert [a.status for a in solution.attempts if a.status != "skipped"][:2] == [
        "solver_error",
        "unsolved",
    ]


def test_work_dir_cleanup_and_keep(detection, fake_solver, index_dir, tmp_path) -> None:
    work = tmp_path / "work"
    plate_solve(detection, _config(fake_solver(), index_dir), work_dir=work)
    assert not work.exists()
    plate_solve(detection, _config(fake_solver(), index_dir, keep_temp=True), work_dir=work)
    assert (work / "attempt-1" / "sources.xyls").is_file()


def test_too_few_sources_is_raised(detection, fake_solver, index_dir) -> None:
    with pytest.raises(TooFewSourcesError):
        plate_solve(
            detection,
            _config(
                fake_solver(), index_dir, min_sources=500, max_sources=500, expanded_max_sources=500
            ),
        )


def test_outputs_when_solved(detection, fake_solver, index_dir, tmp_path) -> None:
    config = _config(fake_solver(), index_dir)
    solution = plate_solve(detection, config)
    paths = save_astrometry_outputs(solution, detection, tmp_path / "out", config)

    document = _strict_json(paths.plate_solution)
    assert document["solved"] is True and document["mode"] == "blind"
    assert document["hints_used"]["sky_position"] is False
    assert document["centre"]["ra_deg"] == pytest.approx(TRUTH_CENTRE[0])
    assert document["geometry"]["field_width_arcmin"] == pytest.approx(WIDTH * 1.5 / 60, rel=1e-3)
    assert document["match_statistics"]["n_matched"] == 12
    assert document["attempts"][0]["command"][0].endswith("solve-field")
    assert document["artifacts"]["wcs"] == "solution.wcs"

    header = fits.getheader(paths.wcs)
    assert header["CTYPE1"].startswith("RA---TAN")
    with paths.selected_csv.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(solution.selection)
    assert float(rows[0]["x"]) == solution.selection.sources[0].x  # canonical, not +1
    selected_doc = _strict_json(paths.selected_json)
    assert selected_doc["summary"]["n_selected"] == len(solution.selection)
    assert selected_doc["summary"]["tier_mode"] == "pooled"
    assert document["attempts"][0]["status"] == "solved"
    with paths.correspondences.open() as fh:
        assert len(list(csv.DictReader(fh))) == 12
    assert "$ " in paths.solver_log.read_text()


def test_overlays_are_aligned(detection, fake_solver, index_dir, tmp_path) -> None:
    config = _config(fake_solver(), index_dir)
    solution = plate_solve(detection, config)
    paths = save_astrometry_outputs(solution, detection, tmp_path / "out", config)

    for path in (paths.selection_overlay, paths.wcs_overlay):
        with Image.open(path) as image:
            assert image.size[0] == WIDTH and image.size[1] > HEIGHT  # legend strip below
    selection_png = np.asarray(Image.open(paths.selection_overlay).convert("RGB"))
    wcs_png = np.asarray(Image.open(paths.wcs_overlay).convert("RGB"))
    radius = max(6.0, 0.8 * detection.aperture_radius)

    def coloured_near(image, x, y, color) -> bool:
        patch = image[round(y) - 1 : round(y) + 2, round(x) - 1 : round(x) + 2]
        return bool(np.any(np.all(patch == color, axis=-1)))

    source = next(
        s
        for s in solution.selection.sources
        if s.tier == TIER_PREFERRED and 20 < s.x < WIDTH - 20 and 20 < s.y < HEIGHT - 20
    )
    assert coloured_near(selection_png, source.x + radius, source.y, TIER_COLORS[TIER_PREFERRED])
    assert coloured_near(selection_png, source.x, source.y - radius, TIER_COLORS[TIER_PREFERRED])
    match = solution.correspondences[0]
    ring = radius + 3 + 1
    assert coloured_near(wcs_png, match.field_x - ring, match.field_y, MATCH_COLOR)
    cx, cy = (WIDTH - 1) / 2, (HEIGHT - 1) / 2
    assert coloured_near(wcs_png, cx, cy, CENTRE_COLOR)


def test_outputs_when_unsolved_remove_stale_solution(
    detection, fake_solver, index_dir, tmp_path
) -> None:
    out = tmp_path / "out"
    config = _config(fake_solver(), index_dir)
    save_astrometry_outputs(plate_solve(detection, config), detection, out, config)
    assert (out / "solution.wcs").exists()

    config = _config(fake_solver("unsolved"), index_dir)
    paths = save_astrometry_outputs(plate_solve(detection, config), detection, out, config)
    assert paths.wcs is None and not (out / "solution.wcs").exists()
    assert not (out / "wcs_overlay.png").exists() and not (out / "correspondences.csv").exists()
    document = _strict_json(paths.plate_solution)
    assert document["solved"] is False and document["centre"] is None


def test_match_statistics_without_correspondences() -> None:
    stats = match_statistics((), 50)
    assert stats.n_matched == 0 and stats.median_residual_arcsec is None
    assert stats.match_fraction == 0.0
    assert read_match_file(None) == (None, {})

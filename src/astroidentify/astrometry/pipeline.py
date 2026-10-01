"""Milestone 3 pipeline: select sources -> write XYLS -> blind solve-field -> parse WCS.

The attempt sequence is deterministic and bounded (at most one solver run per attempt):

0. ``scale_bounds`` - only if camera-derived pixel-scale bounds are configured, and then
   first: the ``bright`` source set with the scale range (much faster than a blind search);
1. ``bright``       - unsaturated and saturated non-edge sources pooled by brightness,
   grid-balanced (``max_sources``), blind. Index files hold the brightest stars of each sky
   region, which in processed consumer images are typically saturated;
2. ``unsaturated``  - unsaturated non-edge sources only, for images whose saturated
   centroids are unreliable, blind;
3. ``expanded``     - all tiers including edge sources, up to ``expanded_max_sources``.

Scheduling: each attempt runs for at most ``timeout_seconds`` (wall clock) and the whole
sequence for at most ``total_timeout_seconds``. A timeout or solver error ends only that
attempt; the next one still runs while budget remains. Attempts with a source set identical
to an earlier one are skipped. Every attempt records its own status
(``solved``/``unsolved``/``timeout``/``solver_error``/``invalid_wcs``/``skipped``/
``budget_exhausted``). No attempt ever receives a sky position or object name.

Typical use::

    detection = detect_sources(preprocess_image(path))
    solution = plate_solve(detection, AstrometryConfig())
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from astroidentify.astrometry.diagnostics import (
    match_statistics,
    read_correspondences,
    read_match_file,
)
from astroidentify.astrometry.selection import select_plate_sources
from astroidentify.astrometry.solver import (
    BACKEND_NAME,
    Prerequisites,
    build_command,
    check_prerequisites,
    format_run_log,
    parse_solver_report,
    run_solver,
)
from astroidentify.astrometry.types import (
    MODE_BLIND,
    MODE_SCALE_CONSTRAINED,
    TIER_EDGE,
    TIER_PREFERRED,
    TIER_SATURATED,
    TIER_SECONDARY,
    PlateSolution,
    SolveAttempt,
    SolverRun,
    SourceSelection,
)
from astroidentify.astrometry.wcs import describe_wcs, load_wcs
from astroidentify.astrometry.xylist import write_xylist
from astroidentify.config import AstrometryConfig
from astroidentify.detection.types import DetectionResult
from astroidentify.exceptions import (
    AstrometryError,
    IndexDataUnavailableError,
    InvalidWCSError,
    SolverNotFoundError,
    SolverProcessError,
    SolverTimeoutError,
)

logger = logging.getLogger(__name__)

STATUS_SOLVED = "solved"
STATUS_UNSOLVED = "unsolved"
STATUS_PREREQUISITES_MISSING = "prerequisites_missing"
STATUS_TIMEOUT = "timeout"
STATUS_SOLVER_ERROR = "solver_error"
STATUS_INVALID_WCS = "invalid_wcs"
STATUS_SKIPPED = "skipped"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"

# Overall status when nothing solved: the most informative attempt outcome wins.
_FAILURE_PRIORITY = (STATUS_INVALID_WCS, STATUS_TIMEOUT, STATUS_UNSOLVED, STATUS_SOLVER_ERROR)
# Do not start an attempt with less remaining total budget than this (seconds).
MIN_ATTEMPT_SECONDS = 5.0

UNSATURATED_TIERS = (TIER_PREFERRED, TIER_SECONDARY)


@dataclass(frozen=True)
class AttemptPlan:
    """One planned solver attempt."""

    name: str
    allowed_tiers: tuple[str, ...]
    max_sources: int
    mode: str
    pooled: bool
    scale_bounds_arcsec: tuple[float, float] | None = None


def plan_attempts(config: AstrometryConfig) -> list[AttemptPlan]:
    """The deterministic attempt sequence for ``config`` (see module docstring)."""
    bright_tiers = UNSATURATED_TIERS + ((TIER_SATURATED,) if config.include_saturated else ())
    all_tiers = bright_tiers + ((TIER_EDGE,) if config.allow_edge_fallback else ())
    plans = []
    if config.scale_bounds is not None:
        plans.append(
            AttemptPlan(
                "scale_bounds",
                bright_tiers,
                config.max_sources,
                MODE_SCALE_CONSTRAINED,
                pooled=True,
                scale_bounds_arcsec=config.scale_bounds,
            )
        )
    plans += [
        AttemptPlan("bright", bright_tiers, config.max_sources, MODE_BLIND, pooled=True),
        AttemptPlan("unsaturated", UNSATURATED_TIERS, config.max_sources, MODE_BLIND, pooled=False),
        AttemptPlan("expanded", all_tiers, config.expanded_max_sources, MODE_BLIND, pooled=True),
    ]
    return plans


def select_for_plan(
    detection: DetectionResult, plan: AttemptPlan, config: AstrometryConfig
) -> SourceSelection:
    """Select the sources for one attempt."""
    return select_plate_sources(
        detection,
        max_sources=plan.max_sources,
        min_sources=config.min_sources,
        allowed_tiers=plan.allowed_tiers,
        grid_shape=config.grid_shape,
        grid_cells=config.grid_cells,
        pooled=plan.pooled,
    )


def plate_solve(
    detection: DetectionResult,
    config: AstrometryConfig | None = None,
    *,
    work_dir: Path | None = None,
) -> PlateSolution:
    """Blindly plate-solve the accepted sources of ``detection``.

    Selection errors (no/too few usable sources) are raised. Solver-side outcomes
    (missing prerequisites, timeout, process failure, unsolved, invalid WCS) are returned
    as a :class:`PlateSolution` with ``status`` and ``error`` set, so the source selection
    and logs can still be saved; call :meth:`PlateSolution.raise_for_status` to turn them
    into exceptions.
    """
    config = config or AstrometryConfig()
    started = time.monotonic()
    plans = plan_attempts(config)
    first_selection = select_for_plan(detection, plans[0], config)
    height, width = detection.plane.shape

    owns_dir = work_dir is None
    root = Path(tempfile.mkdtemp(prefix="astroidentify-solve-")) if owns_dir else Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    log_parts: list[str] = []
    attempts: list[SolveAttempt] = []
    try:
        try:
            prerequisites = check_prerequisites(config, root)
        except (SolverNotFoundError, IndexDataUnavailableError) as exc:
            log_parts.append(f"# prerequisites check failed: {exc}\n")
            return _unsolved(
                STATUS_PREREQUISITES_MISSING, exc, first_selection, attempts, log_parts, started
            )
        return _run_attempts(
            detection, config, plans, first_selection, prerequisites, root, width, height,
            attempts, log_parts, started,
        )  # fmt: skip
    finally:
        if config.keep_temp:
            logger.info("Kept solver working directory %s", root)
        else:
            shutil.rmtree(root, ignore_errors=True)


def _run_attempts(
    detection: DetectionResult,
    config: AstrometryConfig,
    plans: list[AttemptPlan],
    first_selection: SourceSelection,
    prerequisites: Prerequisites,
    root: Path,
    width: int,
    height: int,
    attempts: list[SolveAttempt],
    log_parts: list[str],
    started: float,
) -> PlateSolution:
    seen: dict[tuple, int] = {}
    last_selection = first_selection
    failures: dict[str, AstrometryError] = {}
    for number, plan in enumerate(plans, start=1):
        remaining = config.total_timeout_seconds - (time.monotonic() - started)
        if remaining < MIN_ATTEMPT_SECONDS:
            attempts.append(
                _skipped(number, plan, 0, "total solve budget exhausted", STATUS_BUDGET_EXHAUSTED)
            )
            continue
        try:
            selection = first_selection if number == 1 else select_for_plan(detection, plan, config)
        except AstrometryError as exc:
            attempts.append(_skipped(number, plan, 0, f"selection failed: {exc}"))
            continue
        key = (selection.signature(), plan.scale_bounds_arcsec)
        if key in seen:
            attempts.append(
                _skipped(number, plan, len(selection), f"same inputs as attempt {seen[key]}")
            )
            continue
        seen[key] = number
        last_selection = selection

        timeout = min(config.timeout_seconds, remaining)
        attempt_dir = root / f"attempt-{number}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        xylist = write_xylist(selection, attempt_dir / "sources.xyls")
        command = build_command(
            prerequisites.executable,
            xylist,
            width=width,
            height=height,
            work_dir=attempt_dir,
            engine_config=prerequisites.engine_config,
            cpulimit_seconds=min(config.cpulimit_seconds, timeout),
            scale_bounds_arcsec=plan.scale_bounds_arcsec,
        )
        logger.info(
            "Attempt %d (%s): %d sources, %s, timeout %.0f s",
            number, plan.name, len(selection), plan.mode, timeout,
        )  # fmt: skip
        log_parts.append(
            f"# attempt {number}: {plan.name} ({plan.mode}, {len(selection)} sources, "
            f"timeout {timeout:.0f} s)\n"
        )
        try:
            run = run_solver(command, attempt_dir, timeout, version=prerequisites.version)
        except SolverNotFoundError as exc:
            log_parts.append(f"{exc}\n")
            attempts.append(
                _attempt(
                    number, plan, selection, command, None, STATUS_SOLVER_ERROR, timeout, str(exc)
                )
            )
            return _unsolved(
                STATUS_PREREQUISITES_MISSING,
                exc,
                selection,
                attempts,
                log_parts,
                started,
                prerequisites,
            )
        except (SolverTimeoutError, SolverProcessError) as exc:
            status = STATUS_TIMEOUT if isinstance(exc, SolverTimeoutError) else STATUS_SOLVER_ERROR
            log_parts.append(exc.log or f"{exc}\n")
            attempts.append(
                _attempt(number, plan, selection, command, None, status, timeout, str(exc))
            )
            failures.setdefault(status, exc)
            continue  # one failed attempt must not prevent the fallbacks
        log_parts.append(format_run_log(run))
        if not run.solved:
            attempts.append(
                _attempt(number, plan, selection, command, run, STATUS_UNSOLVED, timeout)
            )
            failures.setdefault(
                STATUS_UNSOLVED, AstrometryError(f"attempt {number} ({plan.name}) did not solve")
            )
            continue
        try:
            result = _solved(
                run, plan, number, selection, attempts, log_parts, started, width, height, timeout
            )
        except InvalidWCSError as exc:
            attempts.append(
                _attempt(
                    number, plan, selection, command, run, STATUS_INVALID_WCS, timeout, str(exc)
                )
            )
            failures.setdefault(STATUS_INVALID_WCS, exc)
            continue
        return result

    status = next((s for s in _FAILURE_PRIORITY if s in failures), STATUS_UNSOLVED)
    summary = ", ".join(f"{a.number}:{a.name}={a.status}" for a in attempts)
    error = failures.get(status) or AstrometryError("no attempt could run")
    exception = type(error)(
        f"no attempt produced a solution ({summary})", log=getattr(error, "log", None)
    )
    return _unsolved(status, exception, last_selection, attempts, log_parts, started, prerequisites)


def _solved(
    run: SolverRun,
    plan: AttemptPlan,
    number: int,
    selection: SourceSelection,
    attempts: list[SolveAttempt],
    log_parts: list[str],
    started: float,
    width: int,
    height: int,
    timeout: float,
) -> PlateSolution:
    """Build the solved result (raises ``InvalidWCSError`` for a missing/bad WCS)."""
    if run.wcs_path is None:
        raise InvalidWCSError("solver reported success but wrote no WCS file")
    wcs, header = load_wcs(run.wcs_path)
    geometry = describe_wcs(wcs, width, height)
    attempts.append(
        _attempt(number, plan, selection, list(run.command), run, STATUS_SOLVED, timeout)
    )
    correspondences = read_correspondences(run.corr_path, selection) if run.corr_path else ()
    log_odds, counts = read_match_file(run.match_path)
    stats = match_statistics(correspondences, len(selection), log_odds, counts)
    return PlateSolution(
        solved=True,
        status=STATUS_SOLVED,
        backend=BACKEND_NAME,
        backend_version=run.version,
        mode=plan.mode,
        constraints=_constraints(plan),
        selection=selection,
        attempts=tuple(attempts),
        solved_attempt=number,
        geometry=geometry,
        wcs=wcs,
        wcs_header=header,
        correspondences=correspondences,
        match_statistics=stats,
        solver_report=parse_solver_report(run.stdout),
        runtime_seconds=time.monotonic() - started,
        solver_log="\n".join(log_parts),
        warnings=selection.warnings,
    )


def _unsolved(
    status: str,
    exc: AstrometryError,
    selection: SourceSelection,
    attempts: list[SolveAttempt],
    log_parts: list[str],
    started: float,
    prerequisites: Prerequisites | None = None,
) -> PlateSolution:
    logger.info("Plate solving %s: %s", status, exc)
    return PlateSolution(
        solved=False,
        status=status,
        backend=BACKEND_NAME,
        backend_version=prerequisites.version if prerequisites else None,
        mode=None,
        constraints=_constraints(None),
        selection=selection,
        attempts=tuple(attempts),
        solved_attempt=None,
        geometry=None,
        wcs=None,
        wcs_header=None,
        correspondences=(),
        match_statistics=None,
        solver_report={},
        runtime_seconds=time.monotonic() - started,
        solver_log="\n".join(log_parts),
        warnings=selection.warnings,
        error=str(exc),
        exception=exc,
    )


def _constraints(plan: AttemptPlan | None) -> dict[str, object]:
    scale = plan.scale_bounds_arcsec if plan else None
    return {
        "position_hint": None,  # never used: no RA/Dec/radius is ever passed
        "object_name_hint": None,  # never used
        "scale_bounds_arcsec_per_pixel": list(scale) if scale else None,
    }


def _attempt(
    number: int,
    plan: AttemptPlan,
    selection: SourceSelection,
    command: list[str],
    run: SolverRun | None,
    status: str,
    timeout: float,
    error: str | None = None,
) -> SolveAttempt:
    return SolveAttempt(
        number=number,
        name=plan.name,
        mode=plan.mode,
        n_sources=len(selection),
        allowed_tiers=selection.allowed_tiers,
        scale_bounds_arcsec=plan.scale_bounds_arcsec,
        solved=status == STATUS_SOLVED,
        runtime_seconds=run.runtime_seconds
        if run
        else timeout
        if status == STATUS_TIMEOUT
        else None,
        command=tuple(command),
        error=error,
        status=status,
        timeout_seconds=timeout,
        tier_mode="pooled" if plan.pooled else "sequential",
    )


def _skipped(
    number: int, plan: AttemptPlan, n_sources: int, reason: str, status: str = STATUS_SKIPPED
) -> SolveAttempt:
    return SolveAttempt(
        number=number,
        name=plan.name,
        mode=plan.mode,
        n_sources=n_sources,
        allowed_tiers=plan.allowed_tiers,
        scale_bounds_arcsec=plan.scale_bounds_arcsec,
        solved=False,
        runtime_seconds=None,
        command=None,
        skipped_reason=reason,
        status=status,
        tier_mode="pooled" if plan.pooled else "sequential",
    )

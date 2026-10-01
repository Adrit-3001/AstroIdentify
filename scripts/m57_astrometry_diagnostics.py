"""Milestone 3 integration-debugging harness (NOT production code).

Runs controlled ``solve-field`` experiments on the Unistellar benchmark image and records
every run in ``outputs/m57-astrometry-diagnostics/run_manifest.json``.

Diagnostic oracle: some runs pass an approximate sky position (``--ra/--dec/--radius``) to
``solve-field`` to test whether the image/source geometry can be solved at all. That
position is defined ONLY in this script, as a diagnostic constant. It is never part of the
production configuration or the ``astroidentify solve`` command, and a position-constrained
run never counts as a Milestone 3 success.

Usage::

    python scripts/m57_astrometry_diagnostics.py --list
    python scripts/m57_astrometry_diagnostics.py raster_known_position xyls_current_known_position
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from astroidentify import preprocess_image
from astroidentify.astrometry.diagnostics import (
    match_statistics,
    read_correspondences,
    read_match_file,
)
from astroidentify.astrometry.pipeline import plan_attempts, select_for_plan
from astroidentify.astrometry.selection import source_tier
from astroidentify.astrometry.solver import parse_solver_report
from astroidentify.astrometry.types import SelectedSource, SourceSelection
from astroidentify.astrometry.wcs import describe_wcs, load_wcs
from astroidentify.astrometry.xylist import to_solver_pixels, write_xylist
from astroidentify.config import AstrometryConfig
from astroidentify.detection import detect_sources
from astroidentify.detection.types import DetectionResult, Source
from astroidentify.serialization import to_jsonable

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png"
OUT = ROOT / "outputs/m57-astrometry-diagnostics"
SOLVE_FIELD = shutil.which("solve-field") or "solve-field"
ENGINE_CONFIG = Path("/etc/astrometry.cfg")

# --- Diagnostic oracle (debugging only; never production input) -------------------------
DIAGNOSTIC_RA_DEG = 283.396
DIAGNOSTIC_DEC_DEG = 33.029
# -------------------------------------------------------------------------------------------


@dataclass
class RunSpec:
    name: str
    kind: str  # "raster" or "xyls"
    description: str
    policy: str = ""
    source_fn: Callable[[DetectionResult], list[Source]] | None = None
    transform: str = "canonical+1"  # XYLS pixel transform (diagnostic flips allowed)
    position_hint: bool = False
    radius_deg: float | None = None
    scale_bounds: tuple[float, float] | None = None
    cpulimit: int = 300
    timeout: int = 900
    extra_args: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- policies


def accepted(detection: DetectionResult) -> list[Source]:
    return [s for s in detection.sources if s.accepted]


def by_flux(sources: list[Source]) -> list[Source]:
    return sorted(sources, key=lambda s: (-s.flux, s.y, s.x))


def flux_top(n: int, *, saturated: bool = True, edge: bool = True):
    def pick(detection: DetectionResult) -> list[Source]:
        pool = [
            s
            for s in accepted(detection)
            if (saturated or not s.saturated) and (edge or not s.edge)
        ]
        return by_flux(pool)[:n]

    return pick


def snr_top(n: int):
    def pick(detection: DetectionResult) -> list[Source]:
        return sorted(accepted(detection), key=lambda s: (-s.snr, s.y, s.x))[:n]

    return pick


def production_selection(detection: DetectionResult, plan_index: int = 0) -> SourceSelection:
    config = AstrometryConfig()
    return select_for_plan(detection, plan_attempts(config)[plan_index], config)


def as_selection(sources: list[Source], detection: DetectionResult) -> SourceSelection:
    """Wrap an arbitrary ordered source list as a SourceSelection (rank = list order)."""
    height, width = detection.plane.shape
    selected = tuple(
        SelectedSource(
            rank=i,
            source_id=s.source_id,
            x=s.x,
            y=s.y,
            flux=s.flux,
            snr=s.snr,
            saturated=s.saturated,
            edge=s.edge,
            tier=source_tier(s),
            cell=(0, 0),
        )
        for i, s in enumerate(sources, start=1)
    )
    return SourceSelection(
        sources=selected,
        image_width=width,
        image_height=height,
        grid_shape=(1, 1),
        allowed_tiers=(),
        max_sources=len(selected),
        n_candidates_by_tier={},
    )


def write_variant_xylist(selection: SourceSelection, path: Path, transform: str) -> None:
    """Production writer for canonical+1; explicit diagnostic flips otherwise."""
    if transform == "canonical+1":
        write_xylist(selection, path)
        return
    ordered = sorted(selection.sources, key=lambda s: s.rank)
    x = np.array([s.x for s in ordered], float)
    y = np.array([s.y for s in ordered], float)
    w, h = selection.image_width, selection.image_height
    sx, sy = to_solver_pixels(x, y)
    if transform == "flip_y":
        sy = (h + 1) - sy
    elif transform == "flip_x":
        sx = (w + 1) - sx
    elif transform == "flip_xy":
        sx, sy = (w + 1) - sx, (h + 1) - sy
    elif transform == "canonical+0":
        sx, sy = x, y
    else:
        raise ValueError(transform)
    cols = [
        fits.Column(name="X", format="D", array=sx),
        fits.Column(name="Y", format="D", array=sy),
        fits.Column(name="FLUX", format="D", array=np.array([s.flux for s in ordered])),
        fits.Column(name="SOURCE_ID", format="K", array=np.array([s.source_id for s in ordered])),
    ]
    table = fits.BinTableHDU.from_columns(cols)
    table.header["IMAGEW"], table.header["IMAGEH"] = w, h
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path, overwrite=True)


# ----------------------------------------------------------------------------- runs

KNOWN = {"position_hint": True, "radius_deg": 2.0, "scale_bounds": (0.3, 2.5)}

RUNS: list[RunSpec] = [
    RunSpec("raster_known_position", "raster", "original PNG, native extraction", **KNOWN),
    RunSpec(
        "xyls_current_known_position",
        "xyls",
        "production attempt-1 selection (balanced preferred 100)",
        policy="production preferred (unsaturated, non-edge, grid-balanced)",
        **KNOWN,
    ),
    RunSpec("xyls_flux100_known_position", "xyls", "top 100 accepted by flux, no balancing",
            policy="flux top 100 (all accepted)", source_fn=flux_top(100), **KNOWN),
    RunSpec("xyls_flux200_known_position", "xyls", "top 200 accepted by flux",
            policy="flux top 200 (all accepted)", source_fn=flux_top(200), **KNOWN),
    RunSpec("xyls_flux400_known_position", "xyls", "top 400 accepted by flux",
            policy="flux top 400 (all accepted)", source_fn=flux_top(400), **KNOWN),
    RunSpec("xyls_unsat_flux100_known_position", "xyls", "top 100 unsaturated by flux",
            policy="flux top 100 unsaturated", source_fn=flux_top(100, saturated=False), **KNOWN),
    RunSpec("xyls_snr100_known_position", "xyls", "top 100 accepted by SNR",
            policy="SNR top 100 (all accepted)", source_fn=snr_top(100), **KNOWN),
]  # fmt: skip
for _transform in ("flip_y", "flip_x", "flip_xy", "canonical+0"):
    RUNS.append(
        RunSpec(
            f"xyls_flux200_{_transform.replace('+', 'plus')}_known_position",
            "xyls",
            f"top 200 by flux, diagnostic transform {_transform}",
            policy="flux top 200 (all accepted)",
            source_fn=flux_top(200),
            transform=_transform,
            **KNOWN,
        )
    )
# Blind runs (no position hint): the production-relevant question.
RUNS += [
    RunSpec("xyls_flux100_blind", "xyls", "top 100 accepted by flux, fully blind",
            policy="flux top 100 (all accepted)", source_fn=flux_top(100), timeout=900),
    RunSpec("xyls_flux100_blind_scale_0.5_1.2", "xyls", "top 100 by flux, blind, scale 0.5-1.2",
            policy="flux top 100 (all accepted)", source_fn=flux_top(100),
            scale_bounds=(0.5, 1.2), timeout=900),
    RunSpec("xyls_flux200_blind", "xyls", "top 200 accepted by flux, fully blind",
            policy="flux top 200 (all accepted)", source_fn=flux_top(200), timeout=900),
    RunSpec("xyls_current_blind_scale_0.75_0.95", "xyls",
            "production selection, blind, scale 0.75-0.95 (reproduces observed failure 5)",
            policy="production preferred (unsaturated, non-edge, grid-balanced)",
            scale_bounds=(0.75, 0.95), timeout=900),
]  # fmt: skip


def pooled_balanced(n: int, tiers: tuple[str, ...]):
    """Production selection code in pooled mode (grid-balanced, brightness-pooled tiers)."""

    def pick(detection: DetectionResult) -> list[Source]:
        from astroidentify.astrometry.selection import select_plate_sources

        selection = select_plate_sources(
            detection, max_sources=n, min_sources=10, allowed_tiers=tiers, pooled=True
        )
        by_id = {s.source_id: s for s in detection.sources}
        return [by_id[s.source_id] for s in sorted(selection.sources, key=lambda s: s.rank)]

    return pick


_BRIGHT_TIERS = ("preferred", "secondary", "saturated")
RUNS += [
    RunSpec("xyls_pooled_balanced100_blind", "xyls",
            "candidate production policy: pooled non-edge tiers, grid-balanced 100, fully blind",
            policy="pooled preferred+secondary+saturated, grid-balanced, non-edge",
            source_fn=pooled_balanced(100, _BRIGHT_TIERS), timeout=900),
    RunSpec("xyls_pooled_balanced100_blind_scale_0.5_1.2", "xyls",
            "candidate production policy, blind, scale 0.5-1.2",
            policy="pooled preferred+secondary+saturated, grid-balanced, non-edge",
            source_fn=pooled_balanced(100, _BRIGHT_TIERS), scale_bounds=(0.5, 1.2), timeout=900),
]  # fmt: skip
RUN_BY_NAME = {run.name: run for run in RUNS}


def register(spec: RunSpec) -> None:
    RUNS.append(spec)
    RUN_BY_NAME[spec.name] = spec


# ----------------------------------------------------------------------------- execution


def build_args(
    spec: RunSpec, input_path: Path, run_dir: Path, width: int, height: int
) -> list[str]:
    out = {k: run_dir / f"solution.{k}" for k in ("wcs", "corr", "match", "solved", "rdls", "axy")}
    args = [SOLVE_FIELD, str(input_path)]
    if spec.kind == "xyls":
        args += ["--width", str(width), "--height", str(height), "--x-column", "X",
                 "--y-column", "Y", "--sort-column", "FLUX",
                 "--no-remove-lines", "--uniformize", "0"]  # fmt: skip
    args += [
        "--config", str(ENGINE_CONFIG), "--dir", str(run_dir), "--out", "solution",
        "--temp-dir", str(run_dir / "tmp"), "--overwrite", "--no-plots", "--crpix-center",
        "--cpulimit", str(spec.cpulimit), "--new-fits", "none",
    ]  # fmt: skip
    for key, path in out.items():
        args += [f"--{key}", str(path)]
    if spec.scale_bounds:
        args += ["--scale-units", "arcsecperpix", "--scale-low", str(spec.scale_bounds[0]),
                 "--scale-high", str(spec.scale_bounds[1])]  # fmt: skip
    if spec.position_hint:
        args += ["--ra", str(DIAGNOSTIC_RA_DEG), "--dec", str(DIAGNOSTIC_DEC_DEG),
                 "--radius", str(spec.radius_deg)]  # fmt: skip
    return args + spec.extra_args


def execute(spec: RunSpec, detection: DetectionResult) -> dict[str, Any]:
    run_dir = OUT / spec.name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "tmp").mkdir(parents=True)
    height, width = detection.plane.shape

    selection = None
    if spec.kind == "xyls":
        if spec.source_fn is None:
            selection = production_selection(detection)
        else:
            selection = as_selection(spec.source_fn(detection), detection)
        input_path = run_dir / "sources.xyls"
        write_variant_xylist(selection, input_path, spec.transform)
        (run_dir / "source_ids.json").write_text(
            json.dumps([s.source_id for s in sorted(selection.sources, key=lambda s: s.rank)])
        )
    else:
        input_path = IMAGE

    args = build_args(spec, input_path, run_dir, width, height)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=spec.timeout, cwd=run_dir, check=False
        )
        returncode, stdout, stderr, status = (
            completed.returncode, completed.stdout, completed.stderr, None,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        returncode, status = None, "timeout"
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    runtime = time.monotonic() - started
    log_path = run_dir / "solver.log"
    log_path.write_text(
        f"$ {shlex.join(args)}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
    )

    solved = (run_dir / "solution.solved").is_file()
    if status is None:
        status = "solved" if solved else ("error" if returncode else "unsolved")
    record: dict[str, Any] = {
        "run": spec.name,
        "input": spec.kind,
        "description": spec.description,
        "source_policy": spec.policy
        or ("native solve-field extraction" if spec.kind == "raster" else ""),
        "source_count": len(selection) if selection is not None else None,
        "saturated_in_list": sum(s.saturated for s in selection.sources) if selection else None,
        "edge_in_list": sum(s.edge for s in selection.sources) if selection else None,
        "pixel_transform": spec.transform if spec.kind == "xyls" else "native",
        "position_hint_used": spec.position_hint,
        "position_hint": [DIAGNOSTIC_RA_DEG, DIAGNOSTIC_DEC_DEG] if spec.position_hint else None,
        "radius_deg": spec.radius_deg,
        "scale_bounds_arcsec": list(spec.scale_bounds) if spec.scale_bounds else None,
        "args": args,
        "cpulimit_s": spec.cpulimit,
        "timeout_s": spec.timeout,
        "runtime_s": round(runtime, 2),
        "exit_status": returncode,
        "status": status,
        "solver_report": parse_solver_report(stdout),
        "log": str(log_path.relative_to(ROOT)),
        "wcs": None,
    }
    if solved and (run_dir / "solution.wcs").is_file():
        wcs, _ = load_wcs(run_dir / "solution.wcs")
        record["wcs"] = str((run_dir / "solution.wcs").relative_to(ROOT))
        record["geometry"] = describe_wcs(wcs, width, height).to_dict()
        corr_selection = selection or as_selection([], detection)
        corr = read_correspondences(run_dir / "solution.corr", corr_selection)
        log_odds, counts = read_match_file(run_dir / "solution.match")
        record["match"] = match_statistics(corr, len(corr_selection), log_odds, counts).to_dict()
    return record


def load_manifest() -> dict[str, Any]:
    path = OUT / "run_manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "purpose": "Milestone 3 integration debugging (diagnostic only; not production)",
        "image": str(IMAGE.relative_to(ROOT)),
        "diagnostic_position_note": "position-hinted runs use a diagnostic oracle defined only "
        "in scripts/m57_astrometry_diagnostics.py; they never count as Milestone 3 success",
        "runs": {},
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list or not args.runs:
        for run in RUNS:
            print(f"{run.name:45s} {run.description}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    detection = detect_sources(preprocess_image(IMAGE))
    manifest = load_manifest()
    manifest["environment"] = {
        "solve_field": SOLVE_FIELD,
        "version": subprocess.run(
            [SOLVE_FIELD, "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "engine_config": str(ENGINE_CONFIG),
        "index_files": sorted(p.name for p in Path("/usr/share/astrometry").glob("index*.fits")),
    }
    for name in args.runs:
        spec = RUN_BY_NAME[name]
        print(f"[{name}] running ...", flush=True)
        record = execute(spec, detection)
        # Each run writes its own record; the manifest is rebuilt from all of them so that
        # concurrently running harness processes cannot overwrite each other.
        (OUT / name / "record.json").write_text(json.dumps(to_jsonable(record), indent=2))
        manifest["runs"] = {
            path.parent.name: json.loads(path.read_text())
            for path in sorted(OUT.glob("*/record.json"))
        }
        (OUT / "run_manifest.json").write_text(json.dumps(to_jsonable(manifest), indent=2))
        geometry = record.get("geometry") or {}
        match = record.get("match") or {}
        print(
            f"[{name}] {record['status']} in {record['runtime_s']} s"
            + (f"; scale {geometry['pixel_scale_arcsec']:.4f}\"/px, centre "
               f"({geometry['centre']['ra_deg']:.4f}, {geometry['centre']['dec_deg']:+.4f}), "
               f"matched {match.get('n_matched')}, "
               f"median resid {match.get('median_residual_arcsec')}"
               if geometry else ""),
            flush=True,
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

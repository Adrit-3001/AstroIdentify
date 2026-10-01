"""Local Astrometry.net ``solve-field`` backend (controlled subprocess boundary).

* The command is an argument list (never a shell string) built by :func:`build_command`.
* It never contains position hints (``--ra``/``--dec``/``--radius``) or object names; the
  only optional constraint is a pixel-scale range (``--scale-low``/``--scale-high``).
* Every output path is absolute: ``solve-field`` resolves explicitly named outputs against
  its working directory, not ``--dir`` (observed with 0.93).
* ``--no-remove-lines --uniformize 0``: the source list is already a filtered, spatially
  balanced detection catalogue, so the solver's raster clean-up steps are unnecessary.
  They also run optional Python helpers that are not needed for source-list solving.
* Solved status comes from the ``--solved`` marker file the solver writes only on success,
  plus a WCS file, not from parsing text output.
* Nothing is downloaded and no web service is contacted.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from astroidentify.astrometry.types import SolverRun
from astroidentify.astrometry.xylist import FLUX_COLUMN, X_COLUMN, Y_COLUMN
from astroidentify.config import AstrometryConfig
from astroidentify.exceptions import (
    IndexDataUnavailableError,
    SolverNotFoundError,
    SolverProcessError,
    SolverTimeoutError,
)

logger = logging.getLogger(__name__)

BACKEND_NAME = "Astrometry.net"
DEFAULT_ENGINE_CONFIG = Path("/etc/astrometry.cfg")
OUTPUT_BASE = "solution"

SETUP_HINT = (
    "Install Astrometry.net and index files, e.g. on Debian/Ubuntu: "
    "'sudo apt install astrometry.net astrometry-data-tycho2' (plus 2MASS index packages "
    "for deeper coverage, see README), or point --index-dir at a directory of index-*.fits "
    "files."
)
_POSITION_HINT_FLAGS = frozenset({"--ra", "--dec", "--radius", "-3", "-4", "-5"})
_VERSION_PATTERN = re.compile(r"Revision\s+([\w.\-]+)|^([\d.]+[\w.\-]*)$", re.MULTILINE)


@dataclass(frozen=True)
class Prerequisites:
    """Resolved solver executable, engine config and index files."""

    executable: Path
    engine_config: Path
    index_files: tuple[Path, ...]
    version: str | None


def find_solve_field(config: AstrometryConfig) -> Path:
    """Locate ``solve-field``.

    Raises:
        SolverNotFoundError: If it is not configured/installed or not executable.
    """
    if config.solve_field_path:
        path = Path(config.solve_field_path).expanduser()
        if not (path.is_file() and os.access(path, os.X_OK)):
            raise SolverNotFoundError(f"solve-field not found or not executable: {path}")
        return path.resolve()
    found = shutil.which("solve-field")
    if found is None:
        raise SolverNotFoundError(f"Astrometry.net 'solve-field' is not on PATH. {SETUP_HINT}")
    return Path(found).resolve()


def solver_version(executable: Path) -> str | None:
    """Best-effort version string from ``solve-field --version``; ``None`` if unknown."""
    try:
        completed = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (completed.stdout + completed.stderr).strip()
    match = _VERSION_PATTERN.search(text)
    if match:
        return match.group(1) or match.group(2)
    return text.splitlines()[0] if text else None


def resolve_engine_config(
    config: AstrometryConfig, work_dir: Path
) -> tuple[Path, tuple[Path, ...]]:
    """Return ``(engine config path, index files it provides)``.

    With ``index_dirs``, a config listing exactly those directories is written to
    ``work_dir``. Otherwise the configured (or system default) config is parsed for its
    ``add_path``/``index`` entries.

    Raises:
        IndexDataUnavailableError: No config exists or it provides no index files.
    """
    if config.index_dirs:
        directories = [Path(d).expanduser().resolve() for d in config.index_dirs]
        lines = [f"add_path {d}" for d in directories] + ["autoindex"]
        path = work_dir / "astrometry.cfg"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        indexes = _index_files_in(directories)
    else:
        path = Path(config.astrometry_config or DEFAULT_ENGINE_CONFIG).expanduser()
        if not path.is_file():
            raise IndexDataUnavailableError(
                f"Astrometry.net engine config not found at {path}. {SETUP_HINT}"
            )
        directories, explicit = parse_engine_config(path)
        indexes = tuple(sorted({*_index_files_in(directories), *explicit}))
    if not indexes:
        searched = ", ".join(str(d) for d in directories) or "(no add_path entries)"
        raise IndexDataUnavailableError(
            f"no Astrometry.net index files found (searched: {searched}; config {path}). "
            f"{SETUP_HINT}"
        )
    return path.resolve(), indexes


def parse_engine_config(path: Path) -> tuple[list[Path], list[Path]]:
    """Extract ``add_path`` directories and explicit ``index`` files from an engine config."""
    directories: list[Path] = []
    explicit: list[Path] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "add_path" and value:
            directories.append(Path(value))
        elif key == "index" and value:
            candidate = Path(value)
            for base in [Path(), *directories]:
                for option in (base / candidate, base / f"{candidate}.fits"):
                    if option.is_file():
                        explicit.append(option.resolve())
    return directories, explicit


def _index_files_in(directories: list[Path]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for directory in directories:
        if directory.is_dir():
            files.update(p.resolve() for p in directory.glob("index*.fits"))
            files.update(p.resolve() for p in directory.glob("index*.fits.fz"))
    return tuple(sorted(files))


def check_prerequisites(config: AstrometryConfig, work_dir: Path) -> Prerequisites:
    """Verify the solver and index data are available (raises clear errors otherwise)."""
    executable = find_solve_field(config)
    engine_config, indexes = resolve_engine_config(config, work_dir)
    version = solver_version(executable)
    logger.info(
        "Using %s %s with %d index files (%s)",
        executable,
        version or "(unknown version)",
        len(indexes),
        engine_config,
    )
    return Prerequisites(executable, engine_config, indexes, version)


def output_paths(work_dir: Path) -> dict[str, Path]:
    """Absolute output file names used for one solver run."""
    base = work_dir.resolve()
    return {
        "wcs": base / f"{OUTPUT_BASE}.wcs",
        "corr": base / f"{OUTPUT_BASE}.corr",
        "match": base / f"{OUTPUT_BASE}.match",
        "solved": base / f"{OUTPUT_BASE}.solved",
        "rdls": base / f"{OUTPUT_BASE}.rdls",
        "axy": base / f"{OUTPUT_BASE}.axy",
        "temp": base / "tmp",
    }


def build_command(
    executable: Path,
    xylist: Path,
    *,
    width: int,
    height: int,
    work_dir: Path,
    engine_config: Path,
    cpulimit_seconds: float,
    scale_bounds_arcsec: tuple[float, float] | None = None,
) -> list[str]:
    """Build the ``solve-field`` argument list for a source-list solve."""
    paths = output_paths(work_dir)
    command = [
        str(executable),
        str(xylist.resolve()),
        "--width", str(width),
        "--height", str(height),
        "--x-column", X_COLUMN,
        "--y-column", Y_COLUMN,
        "--sort-column", FLUX_COLUMN,  # descending (the default): brightest first
        "--config", str(engine_config),
        "--dir", str(work_dir.resolve()),
        "--out", OUTPUT_BASE,
        "--temp-dir", str(paths["temp"]),
        "--overwrite",
        "--no-plots",
        "--no-remove-lines",
        "--uniformize", "0",
        "--crpix-center",
        "--cpulimit", str(round(cpulimit_seconds)),
        "--wcs", str(paths["wcs"]),
        "--corr", str(paths["corr"]),
        "--match", str(paths["match"]),
        "--solved", str(paths["solved"]),
        "--rdls", str(paths["rdls"]),
        "--axy", str(paths["axy"]),
    ]  # fmt: skip
    if scale_bounds_arcsec is not None:
        low, high = scale_bounds_arcsec
        command += [
            "--scale-units", "arcsecperpix",
            "--scale-low", repr(float(low)),
            "--scale-high", repr(float(high)),
        ]  # fmt: skip
    # Defensive invariant: plate solving must never receive a position hint.
    if _POSITION_HINT_FLAGS.intersection(command):
        raise AssertionError("solver command must not contain RA/Dec position hints")
    return command


def run_solver(
    command: list[str],
    work_dir: Path,
    timeout_seconds: float,
    *,
    version: str | None = None,
) -> SolverRun:
    """Run ``solve-field`` and classify the outcome.

    Raises:
        SolverNotFoundError: The executable disappeared or cannot be started.
        SolverTimeoutError: The wall-clock limit was exceeded (process killed).
        SolverProcessError: Non-zero exit status.
    """
    paths = output_paths(work_dir)
    paths["temp"].mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SolverNotFoundError(f"could not start solver: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        log = _format_log(command, None, _text(exc.stdout), _text(exc.stderr), timeout_seconds)
        raise SolverTimeoutError(
            f"solve-field exceeded the {timeout_seconds:g} s time limit and was stopped",
            log=log,
        ) from exc
    runtime = time.monotonic() - started
    if completed.returncode != 0:
        tail = "\n".join(completed.stderr.strip().splitlines()[-5:])
        raise SolverProcessError(
            f"solve-field exited with status {completed.returncode}: {tail or '(no stderr)'}",
            log=_format_log(
                command, completed.returncode, completed.stdout, completed.stderr, runtime
            ),
        )
    solved = paths["solved"].is_file()
    return SolverRun(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        runtime_seconds=runtime,
        solved=solved,
        wcs_path=paths["wcs"] if paths["wcs"].is_file() else None,
        corr_path=paths["corr"] if paths["corr"].is_file() else None,
        match_path=paths["match"] if paths["match"].is_file() else None,
        version=version,
    )


def format_run_log(run: SolverRun) -> str:
    """Human-readable log block for one solver run."""
    return _format_log(
        list(run.command), run.returncode, run.stdout, run.stderr, run.runtime_seconds
    )


def _format_log(
    command: list[str], returncode: int | None, stdout: str, stderr: str, runtime: float
) -> str:
    return (
        f"$ {shlex.join(command)}\n"
        f"# exit status: {returncode if returncode is not None else 'killed (timeout)'}"
        f"; runtime {runtime:.2f} s\n"
        f"--- stdout ---\n{stdout.rstrip()}\n--- stderr ---\n{stderr.rstrip()}\n"
    )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def parse_solver_report(stdout: str) -> dict[str, object]:
    """Extract the solver's own summary lines (raw values, for provenance).

    Note: Astrometry.net's "up" is the FITS +y direction (towards higher row numbers), the
    opposite of AstroIdentify's displayed up; these values are recorded verbatim, while
    AstroIdentify's own geometry is derived from the WCS.
    """
    report: dict[str, object] = {}
    patterns = {
        "field_center_deg": r"Field center: \(RA,Dec\) = \(([-\d.]+), ([-\d.]+)\) deg",
        "field_size": r"Field size: ([\d.]+) x ([\d.]+) (\w+)",
        "rotation_up_deg_east_of_north": r"Field rotation angle: up is ([-\d.]+) degrees E of N",
        "parity": r"Field parity: (\w+)",
        "log_odds": r"log-odds ratio ([-\d.]+)",
        "solved_with_index": r"solved with index ([\w.\-]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, stdout)
        if not match:
            continue
        groups = match.groups()
        if key == "field_center_deg":
            report[key] = [float(groups[0]), float(groups[1])]
        elif key == "field_size":
            report[key] = {
                "width": float(groups[0]),
                "height": float(groups[1]),
                "units": groups[2],
            }
        elif key in ("rotation_up_deg_east_of_north", "log_odds"):
            report[key] = float(groups[0])
        else:
            report[key] = groups[0]
    return report

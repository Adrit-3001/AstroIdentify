from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest

from astroidentify.astrometry.selection import select_from_sources
from astroidentify.astrometry.solver import (
    build_command,
    check_prerequisites,
    find_solve_field,
    output_paths,
    parse_engine_config,
    parse_solver_report,
    resolve_engine_config,
    run_solver,
)
from astroidentify.astrometry.types import TIER_PREFERRED
from astroidentify.astrometry.xylist import write_xylist
from astroidentify.config import AstrometryConfig
from astroidentify.exceptions import (
    ConfigurationError,
    IndexDataUnavailableError,
    SolverNotFoundError,
    SolverProcessError,
    SolverTimeoutError,
)
from tests.astrometry.fixtures import HEIGHT, WIDTH
from tests.astrometry.test_selection import _src


@pytest.fixture
def xylist(tmp_path: Path) -> Path:
    sources = [_src(i, 20.0 * i, 15.0 * i, 1000.0 / i) for i in range(1, 11)]
    selection = select_from_sources(
        sources, width=WIDTH, height=HEIGHT, max_sources=10, min_sources=3,
        allowed_tiers=(TIER_PREFERRED,), grid_shape=(2, 2),
    )  # fmt: skip
    return write_xylist(selection, tmp_path / "sources.xyls")


def _command(executable: Path, xylist: Path, work: Path, scale=None) -> list[str]:
    return build_command(
        executable, xylist, width=WIDTH, height=HEIGHT, work_dir=work,
        engine_config=work / "astrometry.cfg", cpulimit_seconds=60, scale_bounds_arcsec=scale,
    )  # fmt: skip


def test_command_is_blind_argument_list(tmp_path: Path, xylist: Path) -> None:
    command = _command(Path("/opt/bin/solve-field"), xylist, tmp_path)
    assert command[0] == "/opt/bin/solve-field" and command[1] == str(xylist.resolve())
    joined = " ".join(command)
    for forbidden in ("--ra", "--dec", "--radius", "--scale-low", "--scale-high"):
        assert forbidden not in command, forbidden
    assert "M57" not in joined and "ring" not in joined.lower()
    options = dict(itertools.pairwise(command[2:]))
    assert options["--width"] == str(WIDTH) and options["--height"] == str(HEIGHT)
    assert options["--x-column"] == "X" and options["--y-column"] == "Y"
    assert options["--sort-column"] == "FLUX"
    assert "--no-plots" in command and "--overwrite" in command
    for flag in ("--wcs", "--corr", "--match", "--solved"):
        assert Path(options[flag]).is_absolute()  # solve-field resolves them against cwd


def test_scale_constrained_command(tmp_path: Path, xylist: Path) -> None:
    command = _command(Path("solve-field"), xylist, tmp_path, scale=(0.5, 2.0))
    i = command.index("--scale-units")
    assert command[i : i + 6] == [
        "--scale-units", "arcsecperpix", "--scale-low", "0.5", "--scale-high", "2.0",
    ]  # fmt: skip


def test_solver_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SolverNotFoundError, match="not on PATH"):
        find_solve_field(AstrometryConfig())
    with pytest.raises(SolverNotFoundError, match="not found or not executable"):
        find_solve_field(AstrometryConfig(solve_field_path=str(tmp_path / "nope")))


def test_index_data_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(IndexDataUnavailableError, match=r"no Astrometry\.net index files"):
        resolve_engine_config(AstrometryConfig(index_dirs=(str(empty),)), tmp_path)
    with pytest.raises(IndexDataUnavailableError, match="config not found"):
        resolve_engine_config(AstrometryConfig(astrometry_config=str(tmp_path / "x.cfg")), tmp_path)


def test_engine_config_parsing(tmp_path: Path, index_dir: Path) -> None:
    cfg = tmp_path / "astrometry.cfg"
    cfg.write_text(f"# comment\ncpulimit 300\nadd_path {index_dir}  # trailing\nautoindex\n")
    directories, explicit = parse_engine_config(cfg)
    assert directories == [index_dir] and explicit == []
    path, indexes = resolve_engine_config(AstrometryConfig(astrometry_config=str(cfg)), tmp_path)
    assert path == cfg.resolve() and [p.name for p in indexes] == ["index-9999.fits"]


def test_generated_engine_config_for_index_dirs(tmp_path: Path, index_dir: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    path, indexes = resolve_engine_config(AstrometryConfig(index_dirs=(str(index_dir),)), work)
    assert path.read_text() == f"add_path {index_dir.resolve()}\nautoindex\n"
    assert len(indexes) == 1


def test_prerequisites_and_version(fake_solver, index_dir: Path, tmp_path: Path) -> None:
    config = AstrometryConfig(solve_field_path=str(fake_solver()), index_dirs=(str(index_dir),))
    prerequisites = check_prerequisites(config, tmp_path)
    assert prerequisites.version == "0.93-fake"


def _run(fake_solver, mode: str, xylist: Path, work: Path, timeout: float = 30.0):
    executable = fake_solver(mode)
    return run_solver(_command(executable, xylist, work), work, timeout, version="0.93-fake")


def test_successful_solve(fake_solver, xylist: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    run = _run(fake_solver, "solve", xylist, work)
    assert run.solved and run.returncode == 0
    assert run.wcs_path == output_paths(work)["wcs"] and run.wcs_path.is_file()
    assert run.corr_path is not None and run.match_path is not None
    assert "Field center" in run.stdout
    report = parse_solver_report(run.stdout)
    assert report["parity"] == "neg" and report["log_odds"] == 123.4


def test_unsolved_is_not_an_error(fake_solver, xylist: Path, tmp_path: Path) -> None:
    run = _run(fake_solver, "unsolved", xylist, tmp_path)
    assert not run.solved and run.wcs_path is None


def test_process_failure_keeps_log(fake_solver, xylist: Path, tmp_path: Path) -> None:
    with pytest.raises(SolverProcessError, match="status 3") as excinfo:
        _run(fake_solver, "fail", xylist, tmp_path)
    assert "something broke" in excinfo.value.log and "$ " in excinfo.value.log


def test_timeout(fake_solver, xylist: Path, tmp_path: Path) -> None:
    with pytest.raises(SolverTimeoutError, match="time limit") as excinfo:
        _run(fake_solver, "sleep", xylist, tmp_path, timeout=1.0)
    assert "killed (timeout)" in excinfo.value.log


def test_missing_executable_at_run_time(xylist: Path, tmp_path: Path) -> None:
    command = _command(tmp_path / "vanished", xylist, tmp_path)
    with pytest.raises(SolverNotFoundError):
        run_solver(command, tmp_path, 5.0)


def test_no_shell_is_used(monkeypatch: pytest.MonkeyPatch, xylist: Path, tmp_path: Path) -> None:
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs, args=args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_solver(_command(Path("solve-field"), xylist, tmp_path), tmp_path, 5.0)
    assert isinstance(seen["args"], list) and not seen.get("shell", False)
    assert seen["cwd"] == tmp_path and seen["timeout"] == 5.0


def test_config_validation() -> None:
    with pytest.raises(ConfigurationError):
        AstrometryConfig(scale_low_arcsec=1.0)
    with pytest.raises(ConfigurationError):
        AstrometryConfig(scale_low_arcsec=2.0, scale_high_arcsec=1.0)
    with pytest.raises(ConfigurationError):
        AstrometryConfig(max_sources=5, min_sources=10)
    assert AstrometryConfig(scale_low_arcsec=0.5, scale_high_arcsec=2).scale_bounds == (0.5, 2)

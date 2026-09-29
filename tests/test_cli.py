from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from astroidentify.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main


def test_success(simple_fits: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert main(["preprocess", str(simple_fits), "--output", str(out)]) == EXIT_OK

    stdout = capsys.readouterr().out
    for label in (
        "Input:",
        "Format: FITS",
        "Dimensions: 160 x 128",
        "Background estimate:",
        "Noise sigma:",
        "Processed array:",
        "Preview:",
        "Metadata:",
    ):
        assert label in stdout
    assert {p.name for p in out.iterdir()} == {"processed.npy", "preview.png", "metadata.json"}


def test_default_output_directory(
    rgb_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["preprocess", str(rgb_jpeg)]) == EXIT_OK
    assert (tmp_path / "outputs" / "rgb" / "metadata.json").is_file()


def test_options_are_passed_through(simple_fits: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    args = ["preprocess", str(simple_fits), "-o", str(out), "--hdu", "0"]
    args += ["--lower-percentile", "2", "--upper-percentile", "98", "--preview-max-size", "40"]
    assert main(args) == EXIT_OK
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["normalization"]["lower_percentile"] == 2.0
    assert meta["normalization"]["upper_percentile"] == 98.0
    assert meta["config"]["fits_hdu"] == 0
    assert meta["preview"]["max_dimension"] == 40


def test_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preprocess", str(tmp_path / "missing.jpg")]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert captured.out == ""


def test_unsupported_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "image.bmp"
    path.write_bytes(b"BM")
    assert main(["preprocess", str(path), "-o", str(tmp_path / "o")]) == EXIT_ERROR
    assert "unsupported file type" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_corrupt_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "broken.fits"
    path.write_bytes(b"\x00" * 100)
    assert main(["preprocess", str(path), "-o", str(tmp_path / "o")]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_invalid_configuration(simple_fits: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["preprocess", str(simple_fits), "--lower-percentile", "90", "--upper-percentile", "10"]
    assert main(args) == EXIT_USAGE
    assert "percentiles" in capsys.readouterr().err


def test_usage_errors_exit_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == EXIT_USAGE
    with pytest.raises(SystemExit) as excinfo:
        main(["preprocess", "x.fits", "--hdu", "not-a-number"])
    assert excinfo.value.code == EXIT_USAGE


def test_module_entry_point(gray_png: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    ok = subprocess.run(
        [sys.executable, "-m", "astroidentify", "preprocess", str(gray_png), "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert "Format: PNG" in ok.stdout

    bad = subprocess.run(
        [sys.executable, "-m", "astroidentify", "preprocess", str(tmp_path / "nope.png")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    assert "not found" in bad.stderr

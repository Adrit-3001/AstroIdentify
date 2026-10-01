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


# --------------------------------------------------------------------------- detect


def _star_field_png(path: Path) -> Path:
    import numpy as np
    from PIL import Image

    from tests.detection.synthetic import grid_stars, render_field

    shape = (160, 200)
    data = render_field(shape, background=20.0, noise=2.0, stars=grid_stars(shape, 40, 150.0, 4.0))
    Image.fromarray(np.clip(np.round(data), 0, 255).astype(np.uint8)).save(path)
    return path


def test_detect_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    image = _star_field_png(tmp_path / "field.png")
    out = tmp_path / "det"
    assert main(["detect", str(image), "--output", str(out)]) == EXIT_OK

    stdout = capsys.readouterr().out
    for label in (
        "Candidates:",
        "Accepted:",
        "Rejected:",
        "Saturated:",
        "Edge flagged:",
        "Median SNR",
        "Median FWHM",
        "Overlay:",
        "Sources:",
    ):
        assert label in stdout
    expected = {
        "sources.csv",
        "sources.json",
        "detection_metadata.json",
        "preprocessing_metadata.json",
        "detected_sources.png",
        "background_map.npy",
        "background_rms.npy",
        "background_map.png",
        "background_rms.png",
    }
    assert {p.name for p in out.iterdir()} == expected
    meta = json.loads((out / "detection_metadata.json").read_text())
    assert meta["summary"]["n_accepted"] >= 12


def test_detect_default_output_and_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = _star_field_png(tmp_path / "field.png")
    monkeypatch.chdir(tmp_path)
    args = ["detect", str(image), "--fwhm", "4", "--detection-sigma", "6", "--min-snr", "8"]
    assert main([*args, "--box-size", "32"]) == EXIT_OK
    meta = json.loads(
        (tmp_path / "outputs" / "field-detection" / "detection_metadata.json").read_text()
    )
    assert meta["fwhm"] == {
        "value": 4.0,
        "method": "configured",
        "n_stars": 0,
        "iterations": 0,
        "spread_16_84": None,
    }
    assert meta["config"]["detection_sigma"] == 6.0 and meta["config"]["min_snr"] == 8.0
    assert meta["config"]["background_box_size"] == 32


def test_detect_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["detect", str(tmp_path / "missing.png")]) == EXIT_ERROR
    assert "not found" in capsys.readouterr().err


def test_detect_invalid_configuration(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    image = _star_field_png(tmp_path / "field.png")
    assert main(["detect", str(image), "--detection-sigma", "-1"]) == EXIT_USAGE
    assert "detection_sigma" in capsys.readouterr().err


# --------------------------------------------------------------------------- solve


def test_solve_success(tmp_path: Path, fake_solver, index_dir, capsys) -> None:
    image = _star_field_png(tmp_path / "field.png")
    out = tmp_path / "astro"
    args = ["solve", str(image), "-o", str(out), "--solve-field", str(fake_solver())]
    assert main([*args, "--index-dir", str(index_dir), "--max-sources", "15"]) == EXIT_OK
    stdout = capsys.readouterr().out
    labels = (
        "Solved: yes", "Backend: Astrometry.net", "Selected sources:", "Matched sources: 12",
        "Centre RA:", "Centre Dec:", "Pixel scale:", "Field:", "Orientation:", "Residual:",
        "Runtime:", "Mode: blind", "WCS:", "Overlay:",
    )  # fmt: skip
    for label in labels:
        assert label in stdout, label
    assert {p.name for p in out.iterdir()} >= {
        "selected_sources.csv", "selected_sources.json", "source_selection.png", "solution.wcs",
        "plate_solution.json", "wcs_overlay.png", "solver.log", "correspondences.csv",
    }  # fmt: skip
    assert not (out / "solver_work").exists()


def test_solve_missing_solver(tmp_path: Path, capsys) -> None:
    image = _star_field_png(tmp_path / "field.png")
    out = tmp_path / "astro"
    code = main(["solve", str(image), "-o", str(out), "--solve-field", str(tmp_path / "nope")])
    assert code == EXIT_ERROR
    captured = capsys.readouterr()
    assert "Solved: no (prerequisites_missing)" in captured.out
    assert "prerequisites missing" in captured.err
    assert (out / "source_selection.png").is_file() and not (out / "solution.wcs").exists()


def test_solve_unsolved_exit_code(tmp_path: Path, fake_solver, index_dir, capsys) -> None:
    image = _star_field_png(tmp_path / "field.png")
    args = [
        "solve",
        str(image),
        "-o",
        str(tmp_path / "a"),
        "--solve-field",
        str(fake_solver("unsolved")),
    ]
    assert main([*args, "--index-dir", str(index_dir)]) == EXIT_ERROR
    assert "Solved: no (unsolved)" in capsys.readouterr().out


def test_solve_usage_errors(tmp_path: Path, capsys) -> None:
    image = _star_field_png(tmp_path / "field.png")
    assert main(["solve", str(image), "--scale-low", "1.0"]) == EXIT_USAGE
    assert "scale_low_arcsec" in capsys.readouterr().err
    with pytest.raises(SystemExit) as excinfo:
        main(["solve", str(image), "--grid", "banana"])
    assert excinfo.value.code == EXIT_USAGE


# --------------------------------------------------------------------------- catalog-match


@pytest.fixture
def fake_gaia(monkeypatch, saved_products):
    """Replace the Gaia provider used by the CLI with an offline fake for the saved field."""
    import numpy as np

    from astroidentify import cli
    from astroidentify.catalogs.types import CatalogTable
    from tests.catalogs.fixtures import FakeProvider

    detection, wcs = saved_products["detection"], saved_products["wcs_obj"]
    stars = [s for s in detection.sources if s.accepted]
    ra, dec = wcs.all_pix2world([s.x for s in stars], [s.y for s in stars], 0)
    n = len(stars)
    rows = CatalogTable({
        "source_id": np.arange(5000, 5000 + n, dtype=np.int64), "ra": ra, "dec": dec,
        "ra_error": np.zeros(n), "dec_error": np.zeros(n),
        "phot_g_mean_mag": np.linspace(10, 14, n),
        "phot_bp_mean_mag": np.full(n, np.nan), "phot_rp_mean_mag": np.full(n, np.nan),
        "pmra": np.zeros(n), "pmdec": np.zeros(n), "parallax": np.zeros(n),
        "ref_epoch": np.full(n, 2016.0),
    })  # fmt: skip
    monkeypatch.setattr(cli, "GaiaDR3Provider", lambda config: FakeProvider(rows))
    return saved_products


def test_catalog_match_success(fake_gaia, tmp_path: Path, capsys) -> None:
    p = fake_gaia
    out = tmp_path / "cat"
    args = ["catalog-match", str(p["image"]), "--plate-solution", str(p["plate"])]
    assert main([*args, "--detections", str(p["sources"]), "-o", str(out), "--no-cache"]) == EXIT_OK
    stdout = capsys.readouterr().out
    labels = (
        "Catalogue: Gaia DR3", "Rows returned:", "Rows in image:", "Accepted detections:",
        "Matches:", "Median residual:", "RMS residual:", "Epoch propagation: no", "Overlay:",
        "Summary:",
    )  # fmt: skip
    for label in labels:
        assert label in stdout, label
    assert {p.name for p in out.iterdir()} >= {
        "catalog_query.json", "gaia_sources.csv", "catalog_matches.csv",
        "catalog_match_summary.json", "catalog_overlay.png",
    }  # fmt: skip


def test_catalog_match_unsolved_plate_fails(saved_products, tmp_path: Path, capsys) -> None:
    p = saved_products
    plate = json.loads(p["plate"].read_text())
    plate["solved"] = False
    p["plate"].write_text(json.dumps(plate))
    args = [
        "catalog-match",
        str(p["image"]),
        "--plate-solution",
        str(p["plate"]),
        "--detections",
        str(p["sources"]),
    ]
    assert main([*args, "-o", str(tmp_path / "c")]) == EXIT_ERROR
    assert "not solved" in capsys.readouterr().err


def test_catalog_match_usage_error(saved_products, capsys) -> None:
    p = saved_products
    args = [
        "catalog-match",
        str(p["image"]),
        "--plate-solution",
        str(p["plate"]),
        "--detections",
        str(p["sources"]),
    ]
    assert main([*args, "--match-radius", "-1"]) == EXIT_USAGE
    assert "match_radius_arcsec" in capsys.readouterr().err


# --------------------------------------------------------------------------- identify


@pytest.fixture
def identify_products(saved_products, tmp_path: Path):
    """Saved astrometry/catalogue directories for the synthetic field."""
    p = saved_products
    astrometry = tmp_path / "astro"
    astrometry.mkdir()
    (astrometry / "plate_solution.json").write_text(p["plate"].read_text())
    (astrometry / "solution.wcs").write_bytes(p["wcs"].read_bytes())
    return {**p, "astrometry": astrometry}


def _fake_simbad(monkeypatch, rows):
    from astroidentify import cli
    from tests.objects.fixtures import FakeObjectProvider

    monkeypatch.setattr(cli, "SimbadProvider", lambda config: FakeObjectProvider(rows))


def _identify_args(p, out):
    return ["identify", str(p["image"]), "--astrometry", str(p["astrometry"]),
            "--detections", str(p["sources"]), "-o", str(out), "--no-cache"]  # fmt: skip


def test_identify_success(identify_products, monkeypatch, tmp_path: Path, capsys) -> None:
    from tests.objects.fixtures import row_at_pixel

    p = identify_products
    wcs = p["wcs_obj"]
    rows = [row_at_pixel(wcs, 1, 80, 60, "PN", ids="NGC 1|NAME Test Nebula",
                         galdim_majaxis=0.5, galdim_minaxis=0.5, galdim_angle=0),
            row_at_pixel(wcs, 2, 150, 100, "*")]  # fmt: skip
    _fake_simbad(monkeypatch, rows)
    out = tmp_path / "objects"
    assert main(_identify_args(p, out)) == EXIT_OK
    stdout = capsys.readouterr().out
    for label in ("Catalogue:", "WCS:", "Rows returned: 2", "Retained (non-stellar types): 1",
                  "NGC 1", "Overlay:", "Summary:"):  # fmt: skip
        assert label in stdout, label
    assert "confidence" not in stdout.lower()
    assert (out / "identification_summary.json").is_file()
    assert (out / "object_overlay.png").is_file()


def test_identify_valid_empty_field(identify_products, monkeypatch, tmp_path: Path, capsys):
    _fake_simbad(monkeypatch, [])
    assert main(_identify_args(identify_products, tmp_path / "o")) == EXIT_OK
    assert "Retained (non-stellar types): 0" in capsys.readouterr().out


def test_identify_missing_wcs(saved_products, tmp_path: Path, capsys) -> None:
    args = ["identify", str(saved_products["image"]), "-o", str(tmp_path / "o"), "--no-cache"]
    assert main(args) == EXIT_ERROR
    assert "no WCS found" in capsys.readouterr().err


def test_identify_network_failure(identify_products, monkeypatch, tmp_path: Path, capsys):
    from astroidentify import cli
    from astroidentify.exceptions import CatalogQueryError

    class Down:
        def query(self, region):
            raise CatalogQueryError("could not reach the SIMBAD (CDS) TAP: offline")

    monkeypatch.setattr(cli, "SimbadProvider", lambda config: Down())
    assert main(_identify_args(identify_products, tmp_path / "o")) == EXIT_ERROR
    assert "could not reach" in capsys.readouterr().err
    assert not (tmp_path / "o" / "identification_summary.json").exists()


def test_identify_offline_miss_and_usage(identify_products, tmp_path: Path, capsys) -> None:
    p = identify_products
    base = ["identify", str(p["image"]), "--astrometry", str(p["astrometry"])]
    cache = ["--cache-dir", str(tmp_path / "empty-cache")]
    assert main([*base, *cache, "--offline", "-o", str(tmp_path / "o")]) == EXIT_ERROR
    assert "offline mode" in capsys.readouterr().err
    assert main([*base, "--offline", "--no-cache"]) == EXIT_USAGE
    assert main([*base, *cache, "--offline", "--refresh"]) == EXIT_USAGE

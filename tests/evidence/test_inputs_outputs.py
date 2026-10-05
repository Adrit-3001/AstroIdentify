"""Inputs (residual applicability, local residuals), artifacts, overlay and CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astroidentify.cli import EXIT_ERROR, EXIT_OK, main
from astroidentify.config import EvidenceConfig
from astroidentify.evidence.astrometry import LocalResiduals, field_astrometry
from astroidentify.evidence.features import (
    RESIDUALS_FINAL_WCS,
    RESIDUALS_INPUT_OFFSET,
    RESIDUALS_NONE,
    RESIDUALS_PLATE_SOLVER,
    EvidenceInputs,
    GaiaMatches,
    residual_relation,
)
from astroidentify.evidence.outputs import CSV_COLUMNS
from astroidentify.evidence.overlay import LEVEL_COLORS, render_evidence_overlay
from astroidentify.evidence.types import SUPPORT_LEVELS, EvidenceResult
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.serialization import sha256_file
from astroidentify.types import ImageFormat
from tests.evidence.fixtures import H, W, detections, field, obj, run


def _wcs_file(path: Path, value: float) -> Path:
    header = fits.Header()
    header["TEST"] = value
    fits.PrimaryHDU(header=header).writeto(path)
    return path


def test_residual_relation(tmp_path) -> None:
    catalog = tmp_path / "cat"
    catalog.mkdir()
    refined = _wcs_file(catalog / "refined_solution.wcs", 1.0)
    blind = _wcs_file(tmp_path / "solution.wcs", 2.0)
    summary = {"inputs": {"wcs": str(blind)}, "wcs_refinement": {"applied": True}}
    used_refined = {"sha256": sha256_file(refined), "source": "catalog_refined"}
    used_blind = {"sha256": sha256_file(blind), "source": "plate_solution"}
    assert residual_relation(used_refined, catalog, summary, None) == RESIDUALS_FINAL_WCS
    assert residual_relation(used_blind, catalog, summary, None) == RESIDUALS_INPUT_OFFSET
    unrefined = {**summary, "wcs_refinement": {"applied": False}}
    assert residual_relation(used_blind, catalog, unrefined, None) == RESIDUALS_FINAL_WCS
    plate = {"solved": True}
    assert residual_relation(used_blind, None, None, plate) == RESIDUALS_PLATE_SOLVER
    assert (
        residual_relation({"sha256": "x", "source": "explicit"}, catalog, summary, plate)
        == RESIDUALS_NONE
    )


def _inputs(relation, catalog=None, plate=None, gaia=None) -> EvidenceInputs:
    image = image_from_array(
        np.zeros((H, W), np.float32), image_format=ImageFormat.FITS, display_origin="upper"
    )
    summary = {
        "image": {"pixel_scale_arcsec": 1.0, "width": W, "height": H},
        "wcs": {"source": "catalog_refined", "refined": True},
        "inputs": {},
    }
    return EvidenceInputs(
        image, np.zeros((H, W)), [], summary, catalog, gaia, relation, plate, None, None, 10.0, {}
    )


def test_field_astrometry_grades_by_source() -> None:
    config = EvidenceConfig()
    catalog = {
        "summary": {"matches": 630, "median_residual_px": 0.79},
        "wcs_refinement": {"input_wcs_median_offset_arcsec": 2.2, "registration_pairs": 500},
    }
    plate = {"match_statistics": {"n_matched": 24, "median_residual_px": 0.77}}
    assert field_astrometry(_inputs(RESIDUALS_FINAL_WCS, catalog), config).grade == "precise"
    offset = field_astrometry(_inputs(RESIDUALS_INPUT_OFFSET, catalog), config)
    assert offset.grade == "adequate" and offset.r50_px == pytest.approx(2.2)
    solver = field_astrometry(_inputs(RESIDUALS_PLATE_SOLVER, None, plate), config)
    assert solver.grade == "adequate" and solver.r50_source == "plate_solver"  # capped
    none = field_astrometry(_inputs(RESIDUALS_NONE, catalog, plate), config)
    assert none.grade == "unavailable" and none.r50_px is None  # nothing invented
    poor = {"summary": {"matches": 5, "median_residual_px": 0.5}}
    assert field_astrometry(_inputs(RESIDUALS_FINAL_WCS, poor), config).grade == "poor"


def test_local_residuals_follow_position_and_fall_back_to_field() -> None:
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 400, 200)
    y = rng.uniform(0, 300, 200)
    residual = np.where(x < 200, 0.6, 3.0)
    gaia = GaiaMatches(np.arange(200), np.arange(200) + 10**6, x, y, residual)
    inputs = _inputs(
        RESIDUALS_FINAL_WCS, {"summary": {"matches": 200, "median_residual_px": 1.0}}, gaia=gaia
    )
    config = EvidenceConfig()
    f = field_astrometry(inputs, config)
    local = LocalResiduals(inputs, config)
    assert local.scale_at(f, 50, 150, 1.0)[:2] == (pytest.approx(0.6), "local_gaia")
    assert local.scale_at(f, 350, 150, 1.0)[0] == pytest.approx(3.0)
    sparse = LocalResiduals(
        _inputs(
            RESIDUALS_FINAL_WCS,
            {},
            gaia=GaiaMatches(
                np.arange(15), np.arange(15), np.full(15, 5.0), np.full(15, 5.0), np.ones(15)
            ),
        ),
        config,
    )
    assert sparse.scale_at(f, 390, 290, 1.0)[1] == "field_gaia"  # matches too far away


def test_overlay_marker_is_centred_and_dimensions_unchanged() -> None:
    evidence = run([obj(1, 300, 200)], dets=detections([(300.2, 200.0), (380, 280)]))
    result = EvidenceResult(tuple(evidence), W, H, 1.0, field(), EvidenceConfig(), {})
    image = image_from_array(
        np.full((H, W), 10.0, np.float32), image_format=ImageFormat.FITS, display_origin="upper"
    )
    overlay, drawn = render_evidence_overlay(image, result, {}, max_labels=0)
    assert overlay.size == (W, H) and drawn == (1,)
    window = np.asarray(overlay)[160:241, 260:341]  # away from the legend box
    ys, xs = np.nonzero(np.all(window == LEVEL_COLORS["strong"], axis=2))
    assert (xs.min() + xs.max()) / 2 + 260 == 300 and (ys.min() + ys.max()) / 2 + 160 == 200


# --------------------------------------------------------------------------- end to end


@pytest.fixture
def object_products(saved_products, tmp_path, monkeypatch):
    """Milestone 3 + 5 directories for the synthetic field (fake SIMBAD, no network)."""
    from astroidentify import cli
    from tests.objects.fixtures import FakeObjectProvider, row_at_pixel

    p = saved_products
    astrometry = tmp_path / "astro"
    astrometry.mkdir()
    plate = json.loads(p["plate"].read_text())
    plate["match_statistics"] = {"n_matched": 30, "median_residual_px": 0.6}
    (astrometry / "plate_solution.json").write_text(json.dumps(plate))
    (astrometry / "solution.wcs").write_bytes(p["wcs"].read_bytes())
    star = next(s for s in p["detection"].sources if s.accepted)
    rows = [
        row_at_pixel(p["wcs_obj"], 1, star.x, star.y, "QSO", ids="NGC 1"),
        row_at_pixel(p["wcs_obj"], 2, 60, 60, "G"),
        # A bright named star: Milestone 5 excludes category "star"; Milestone 6 must not
        # assess or resurrect it (the Vega-field behaviour).
        row_at_pixel(p["wcs_obj"], 3, 120, 90, "*", ids="NAME Bright Star"),
    ]
    monkeypatch.setattr(cli, "SimbadProvider", lambda config: FakeObjectProvider(rows))
    objects = tmp_path / "objects"
    args = [
        "identify",
        str(p["image"]),
        "--astrometry",
        str(astrometry),
        "--detections",
        str(p["sources"]),
        "-o",
        str(objects),
        "--no-cache",
    ]
    assert main(args) == EXIT_OK
    return {**p, "objects": objects, "astrometry": astrometry}


def test_assess_cli_success_offline(object_products, tmp_path, monkeypatch, capsys) -> None:
    import astroidentify.catalogs.tap as tap

    def no_network(*args, **kwargs):
        raise AssertionError("assess must not use the network")

    monkeypatch.setattr(tap, "tap_fetch", no_network)
    p = object_products
    out = tmp_path / "evidence"
    assert (
        main(["assess", str(p["image"]), "--objects", str(p["objects"]), "-o", str(out)]) == EXIT_OK
    )
    stdout = capsys.readouterr().out
    assert "Objects assessed: 2" in stdout and "not probabilities" in stdout
    m5 = json.loads((p["objects"] / "catalog_objects.json").read_text())["objects"]
    star = next(o for o in m5 if o["catalogue_id"] == 3)
    assert star["status"] == "in_field_type_excluded" and star["category"] == "star"
    assert {f.name for f in out.iterdir()} == {
        "object_evidence.csv",
        "object_evidence.json",
        "evidence_summary.json",
        "evidence_overlay.png",
    }
    document = json.loads((out / "object_evidence.json").read_text())
    assert document["evidence_version"] and len(document["objects"]) == 2
    assert 3 not in {r["catalogue_id"] for r in document["objects"]}
    for record in document["objects"]:
        assert record["support_level"] in SUPPORT_LEVELS and record["evidence_score"] is None
        assert record["reason_codes"] and record["explanation"]
        assert record["astrometry"]["r50_source"] == "plate_solver"  # no Gaia run
    qso = next(r for r in document["objects"] if r["catalogue_id"] == 1)
    assert qso["compact"]["detection_source_id"] is not None
    with (out / "object_evidence.csv").open() as fh:
        assert tuple(next(csv.reader(fh))) == CSV_COLUMNS
    summary = json.loads((out / "evidence_summary.json").read_text())
    assert "not probabilities" in summary["statement"]
    assert sum(summary["counts"]["by_support_level"].values()) == 2
    assert "%" not in json.dumps(summary["counts"])


def test_assess_cli_failures(object_products, tmp_path, capsys) -> None:
    p = object_products
    assert main(["assess", str(p["image"]), "--objects", str(tmp_path / "missing")]) == EXIT_ERROR
    assert "could not read" in capsys.readouterr().err
    from PIL import Image

    other = tmp_path / "other.png"
    pixels = np.asarray(Image.open(p["image"])).copy()
    pixels[0, 0] = 255 - pixels[0, 0]  # a different image of the same size
    Image.fromarray(pixels).save(other)
    assert main(["assess", str(other), "--objects", str(p["objects"])]) == EXIT_ERROR
    assert "different image" in capsys.readouterr().err

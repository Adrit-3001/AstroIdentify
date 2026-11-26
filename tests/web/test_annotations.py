"""From synthetic pipeline outputs to markers, outcomes and the annotated PNG (offline)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astroidentify.objects.overlay import CATEGORY_COLORS
from astroidentify.preprocessing.loader import load_image
from astroidentify.web.annotations import (
    StepOutcome,
    build_result,
    display_pixels,
    friendly_error,
    place_label,
    render_annotated,
)

W, H = 64, 48
STEPS = ("detect", "solve", "catalog-match", "identify", "assess")


def _png(tmp_path: Path, value: int = 10) -> Path:
    path = tmp_path / "input.png"
    Image.fromarray(np.full((H, W, 3), value, np.uint8)).save(path)
    return path


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _outputs(tmp_path: Path) -> dict[str, Path]:
    dirs = {s: tmp_path / "steps" / s for s in STEPS}
    _write(
        dirs["detect"] / "sources.json",
        {
            "sources": [
                {"source_id": 1, "x": 10.0, "y": 12.0, "flux": 500.0, "accepted": True},
                {"source_id": 2, "x": 30.0, "y": 20.0, "flux": 300.0, "accepted": True},
                {"source_id": 3, "x": 50.0, "y": 40.0, "flux": 100.0, "accepted": True},
                {"source_id": 4, "x": 5.0, "y": 5.0, "flux": 900.0, "accepted": False},
            ]
        },
    )
    _write(
        dirs["solve"] / "plate_solution.json",
        {
            "solved": True,
            "status": "solved",
            "centre": {"ra_deg": 283.3865, "dec_deg": 33.027},
            "geometry": {
                "field_width_arcmin": 36.6,
                "field_height_arcmin": 27.4,
                "pixel_scale_arcsec": 0.857,
            },
            "match_statistics": {"n_matched": 24, "n_selected": 100},
        },
    )
    dirs["catalog-match"].mkdir(parents=True)
    with (dirs["catalog-match"] / "catalog_matches.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["detection_source_id", "gaia_source_id", "phot_g_mean_mag"])
        writer.writerow([1, 111, 9.5])
    _write(
        dirs["catalog-match"] / "catalog_match_summary.json",
        {"summary": {"matches": 1, "median_residual_arcsec": 0.5}},
    )
    _write(
        dirs["identify"] / "catalog_objects.json",
        {
            "objects": [
                {
                    "retained": True,
                    "catalogue_id": 7,
                    "display_name": "M 57",
                    "common_names": ["Ring Nebula"],
                    "object_type_description": "Planetary Nebula",
                    "category": "planetary_nebula",
                    "projected_x": 32.0,
                    "projected_y": 24.0,
                    "footprint_px": [[28, 24], [32, 20], [36, 24], [32, 28]],
                    "n_references": 800,
                },
                {
                    "retained": True,
                    "catalogue_id": 8,
                    "display_name": "LEDA 1",
                    "common_names": [],
                    "object_type_description": "Galaxy",
                    "category": "galaxy",
                    "projected_x": 12.0,
                    "projected_y": 36.0,
                    "footprint_px": None,
                    "n_references": 2,
                },
                {
                    "retained": False,
                    "catalogue_id": 9,
                    "display_name": "HD 1",
                    "category": "star",
                    "projected_x": 10.0,
                    "projected_y": 12.0,
                },
            ]
        },
    )
    _write(
        dirs["identify"] / "identification_summary.json", {"image": {"pixel_scale_arcsec": 0.857}}
    )
    _write(
        dirs["assess"] / "object_evidence.json",
        {
            "objects": [
                {"catalogue_id": 7, "support_level": "strong", "explanation": "clear"},
                {"catalogue_id": 8, "support_level": "catalogue-only", "explanation": "faint"},
            ]
        },
    )
    return dirs


def _all_ok() -> dict[str, StepOutcome]:
    return {s: StepOutcome(True) for s in STEPS}


def test_identified_result_marks_and_names_only_what_the_pipeline_matched(tmp_path):
    image = load_image(_png(tmp_path))
    result, pixels = build_result(
        image, _outputs(tmp_path), _all_ok(), filename="m57.png", solve_total_timeout_seconds=900
    )
    assert pixels.shape == (H, W, 3) and not result["image"]["display_stretch"]
    assert result["outcome"]["kind"] == "identified"
    counts = result["counts"]
    assert counts["detections"] == 3  # the rejected source is not a detection
    assert counts["matched_stars"] == 1 and counts["detections_marked"] == 2
    assert counts["objects"] == 2 and counts["objects_labelled"] == 1

    by_kind = {}
    for m in result["markers"]:
        by_kind.setdefault(m["kind"], []).append(m)
    assert [(m["x"], m["y"]) for m in by_kind["star"]] == [(10.0, 12.0)]
    assert "name" not in by_kind["star"][0]  # no SIMBAD star name was matched
    assert sorted((m["x"], m["y"]) for m in by_kind["detection"]) == [(30.0, 20.0), (50.0, 40.0)]
    objects = {m["name"]: m for m in by_kind["object"]}
    assert objects["M 57"]["label"] and objects["M 57"]["support"] == "strong"
    assert not objects["LEDA 1"].get("label")  # catalogue-only: marked and listed, not named
    assert result["sky"]["confirmed"] and "Gaia DR3" in result["sky"]["check"]
    assert result["sky"]["ra"].startswith("18h53m")


def test_annotated_png_draws_markers_at_their_coordinates(tmp_path):
    image = load_image(_png(tmp_path))
    result, pixels = build_result(
        image, _outputs(tmp_path), _all_ok(), filename="m57.png", solve_total_timeout_seconds=900
    )
    annotated = render_annotated(pixels, result)
    assert annotated.size == (W, H)
    colour = np.array(CATEGORY_COLORS["planetary_nebula"])
    vertex = np.array(annotated.getpixel((36, 24)))  # a vertex of M 57's outline
    assert np.abs(vertex - colour).sum() < 40
    assert annotated.getpixel((62, 1)) == (10, 10, 10)  # away from all markers: untouched


def test_fits_images_are_flipped_with_their_markers(tmp_path):
    data = np.zeros((H, W), np.float32)
    data[5, 5] = 100  # bottom-left in FITS orientation
    path = tmp_path / "input.fits"
    fits.PrimaryHDU(data).writeto(path)
    image = load_image(path)
    pixels, stretched = display_pixels(image)
    assert stretched and pixels.shape == (H, W)
    assert pixels[H - 1 - 5, 5] == pixels.max()  # now near the bottom, as usually displayed
    result, _ = build_result(
        image, _outputs(tmp_path), _all_ok(), filename="x.fits", solve_total_timeout_seconds=900
    )
    star = next(m for m in result["markers"] if m["kind"] == "star")
    assert star["y"] == (H - 1) - 12.0
    assert result["image"]["flipped_vertically"]


def test_unsolved_image_shows_detections_only_and_explains_why(tmp_path):
    dirs = _outputs(tmp_path)
    _write(
        dirs["solve"] / "plate_solution.json",
        {"solved": False, "status": "timeout", "error": "no attempt produced a solution"},
    )
    outcomes = {"detect": StepOutcome(True), "solve": StepOutcome(False, "plate solving timeout")}
    result, _ = build_result(
        load_image(_png(tmp_path)), dirs, outcomes, filename="x.png", solve_total_timeout_seconds=45
    )
    outcome = result["outcome"]
    assert outcome["kind"] == "not_solved"
    assert "ran out of time" in outcome["message"] and "45 seconds" in outcome["message"]
    assert "3 points of light" in outcome["markers"]
    assert {m["kind"] for m in result["markers"]} == {"detection"}
    assert result["sky"] is None


def test_unconfirmed_sky_position_is_labelled_as_such(tmp_path):
    outcomes = {
        "detect": StepOutcome(True),
        "solve": StepOutcome(True),
        "catalog-match": StepOutcome(False, "malformed catalogue response: <!doctype html>..."),
    }
    result, _ = build_result(
        load_image(_png(tmp_path)),
        _outputs(tmp_path),
        outcomes,
        filename="x.png",
        solve_total_timeout_seconds=900,
    )
    assert result["outcome"]["kind"] == "catalog_unavailable"
    assert "web page" in result["outcome"]["detail"]
    assert not result["sky"]["confirmed"]
    assert "24 of the 100" in result["sky"]["check"] and "unconfirmed" in result["sky"]["check"]
    assert not any(m.get("label") for m in result["markers"])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("could not reach the SIMBAD: [Errno -2] Name or service not known", "DNS"),
        ("SIMBAD query exceeded the 60 s time limit", "didn't answer in time"),
        (None, None),
    ],
)
def test_friendly_error(error, expected):
    text = friendly_error(error)
    assert (text is None) if expected is None else (expected in text)


def test_labels_avoid_each_other_and_are_never_dropped():
    placed = []
    first = place_label((10, 10, 20, 20), (50, 12), placed, 200, 100, 4)
    placed.append(first)
    second = place_label((22, 10, 32, 20), (50, 12), placed, 200, 100, 4)
    assert second is not None
    overlap_w = min(first[2], second[2]) - max(first[0], second[0])
    overlap_h = min(first[3], second[3]) - max(first[1], second[1])
    assert overlap_w <= 0 or overlap_h <= 0
    crowded = place_label((0, 0, 200, 100), (190, 90), [(0, 0, 200, 100)], 200, 100, 4)
    assert crowded is not None  # least-overlap fallback

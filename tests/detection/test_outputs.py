from __future__ import annotations

import csv
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astroidentify.config import DetectionConfig
from astroidentify.detection import detect_sources, save_detection_outputs
from astroidentify.detection.outputs import CSV_COLUMNS, sources_to_csv
from astroidentify.detection.overlay import ACCEPTED_COLOR, render_overlay
from astroidentify.exceptions import OutputError
from astroidentify.types import ImageFormat
from tests.detection.synthetic import Star, as_preprocessed, grid_stars, render_field
from tests.detection.test_filtering import _source

SHAPE = (160, 200)


@pytest.fixture(scope="module")
def result():
    stars = [*grid_stars(SHAPE, 50, 600.0, 4.0, margin=25), Star(150.0, 40.0, 3000.0, 4.0)]
    return detect_sources(as_preprocessed(render_field(SHAPE, stars=stars)))


def _strict_json(path: Path):
    def reject(constant: str) -> None:
        raise ValueError(f"non-standard JSON constant {constant}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def test_all_artifacts_written(result, tmp_path: Path) -> None:
    paths = save_detection_outputs(result, tmp_path / "det")
    for path in paths.all():
        assert path.is_file(), path
    np.testing.assert_array_equal(np.load(paths.background_map), result.background.background)
    np.testing.assert_array_equal(np.load(paths.background_rms), result.background.rms)


def test_sources_csv(result, tmp_path: Path) -> None:
    paths = save_detection_outputs(result, tmp_path / "det")
    with paths.sources_csv.open() as fh:
        rows = list(csv.DictReader(fh))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == len(result.sources)
    for row, source in zip(rows, result.sources, strict=True):
        assert int(row["source_id"]) == source.source_id
        assert float(row["x"]) == pytest.approx(source.x, abs=1e-6)
        assert float(row["y"]) == pytest.approx(source.y, abs=1e-6)
        assert row["accepted"] == ("true" if source.accepted else "false")
        assert row["rejection_reasons"] == ";".join(source.rejection_reasons)


def test_csv_encodes_nan_and_reasons() -> None:
    text = sources_to_csv([_source(sharpness=float("nan"), rejection_reasons=("a", "b"))])
    row = next(csv.DictReader(text.splitlines()))
    assert row["sharpness"] == "" and row["rejection_reasons"] == "a;b"


def test_sources_json(result, tmp_path: Path) -> None:
    document = _strict_json(save_detection_outputs(result, tmp_path / "det").sources_json)
    assert document["coordinate_convention"]["row_zero_displayed_at"] == "top"
    assert len(document["sources"]) == len(result.sources)
    first = document["sources"][0]
    assert first["source_id"] == 1 and isinstance(first["rejection_reasons"], list)
    assert set(document["fields"]) == set(CSV_COLUMNS)


def test_detection_metadata(result, tmp_path: Path) -> None:
    meta = _strict_json(save_detection_outputs(result, tmp_path / "det").metadata)
    summary = meta["summary"]
    for key in (
        "n_candidates",
        "n_accepted",
        "n_rejected",
        "n_saturated",
        "n_edge_flagged",
        "median_snr_accepted",
        "median_fwhm_accepted",
        "background_map",
        "background_rms_map",
        "noise_correlation_factor",
    ):
        assert key in summary
    assert summary["n_candidates"] == summary["n_accepted"] + summary["n_rejected"]
    assert set(summary["background_map"]) == {"min", "median", "max"}
    assert meta["config"] == DetectionConfig().to_dict() | {"sharpness_range": [0.2, 1.0]}
    assert meta["fwhm"]["method"] == "estimated"
    assert isinstance(meta["warnings"], list)
    assert meta["artifacts"]["overlay"] == "detected_sources.png"


def test_overlay_is_full_resolution_and_aligned(result) -> None:
    overlay = np.asarray(render_overlay(result))
    # Image pixels occupy rows 0..H-1 unchanged; the legend strip is appended below.
    assert overlay.shape[1:] == (SHAPE[1], 3) and overlay.shape[0] > SHAPE[0]

    # The unsaturated bright star at (x=150, y=40): its aperture circle passes through
    # (x + r, y) and (x, y - r) in array coordinates (row = y, no flip).
    source = min(result.accepted_sources, key=lambda s: (s.x - 150) ** 2 + (s.y - 40) ** 2)
    r = result.aperture_radius

    def colored_near(x: float, y: float) -> bool:
        rows = slice(round(y) - 1, round(y) + 2)
        cols = slice(round(x) - 1, round(x) + 2)
        return bool(np.any(np.all(overlay[rows, cols] == ACCEPTED_COLOR, axis=-1)))

    assert colored_near(source.x + r, source.y)
    assert colored_near(source.x, source.y - r)
    assert colored_near(source.x - r, source.y)
    assert not colored_near(source.x, source.y)  # centre left visible


def test_overlay_does_not_flip_fits(tmp_path: Path) -> None:
    star = Star(60.0, 20.0, 2000.0, 4.0)  # near the top of the array
    preprocessed = as_preprocessed(render_field((100, 120), stars=[star]))
    assert preprocessed.image.format is ImageFormat.FITS
    detection = detect_sources(preprocessed, DetectionConfig(fwhm=4.0))
    overlay = np.asarray(render_overlay(detection))
    r = detection.aperture_radius
    ring = overlay[round(20 - r) - 1 : round(20 - r) + 2, 59:62]
    assert np.any(np.all(ring == ACCEPTED_COLOR, axis=-1))


def test_overlay_png_matches_image_size(result, tmp_path: Path) -> None:
    paths = save_detection_outputs(result, tmp_path / "det")
    with Image.open(paths.overlay) as image:
        assert image.size[0] == SHAPE[1] and image.size[1] > SHAPE[0]


def test_refuses_to_overwrite_source(result, tmp_path: Path) -> None:
    source = tmp_path / "sources.csv"
    source.write_text("pretend this is the input image")
    image = dataclasses.replace(result.preprocessing.image, source_path=source)
    preprocessing = dataclasses.replace(result.preprocessing, image=image)
    detection = dataclasses.replace(result, preprocessing=preprocessing)
    with pytest.raises(OutputError, match="overwrite the source"):
        save_detection_outputs(detection, tmp_path)
    assert source.read_text() == "pretend this is the input image"


def test_output_path_is_a_file(result, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(OutputError, match="not a directory"):
        save_detection_outputs(result, blocker)

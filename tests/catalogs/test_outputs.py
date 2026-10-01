from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astroidentify.catalogs.outputs import MATCH_COLUMNS, save_catalog_outputs
from astroidentify.catalogs.overlay import MATCH_COLOR, RESIDUAL_COLOR
from astroidentify.catalogs.pipeline import match_catalog
from astroidentify.config import CatalogConfig
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.types import ImageFormat
from tests.catalogs.fixtures import FakeProvider, H, W, sources_at, synthetic_field


def _strict_json(path: Path):
    def reject(constant: str) -> None:
        raise ValueError(constant)

    return json.loads(path.read_text(), parse_constant=reject)


@pytest.fixture
def run(tmp_path: Path):
    field = synthetic_field()
    sources = sources_at(field.star_xy + 0.2)
    config = CatalogConfig()
    result = match_catalog(sources, field.wcs, W, H, FakeProvider(field.rows), config)
    image = image_from_array(
        np.full((H, W), 10.0), image_format=ImageFormat.PNG, source_path=tmp_path / "img.png"
    )
    paths = save_catalog_outputs(result, image, tmp_path / "out", config, {"image": "img.png"})
    return result, paths


def test_artifacts_written(run) -> None:
    _, paths = run
    for path in (
        paths.query,
        paths.sources,
        paths.matches,
        paths.summary,
        paths.overlay,
        paths.refined_wcs,
    ):
        assert path is not None and path.is_file(), path


def test_query_json_provenance(run) -> None:
    _, paths = run
    query = _strict_json(paths.query)
    assert query["release"] == "Gaia DR3" and query["table"] == "gaiadr3.gaia_source"
    assert query["region"]["shape"] == "cone" and query["region"]["radius_deg"] > 0
    assert query["origin"] == "live" and query["truncated"] is False
    assert {"source_id", "ra", "dec", "pmra", "parallax"} <= set(query["columns"])
    assert query["epoch"]["propagated"] is False


def test_match_csv_columns_and_ids(run) -> None:
    result, paths = run
    rows = list(csv.DictReader(paths.matches.open()))
    assert tuple(rows[0].keys()) == MATCH_COLUMNS
    assert len(rows) == result.summary.matches
    assert [int(r["detection_source_id"]) for r in rows] == sorted(
        int(r["detection_source_id"]) for r in rows
    )
    for column in (
        "detection_source_id",
        "gaia_source_id",
        "residual_px",
        "residual_arcsec",
        "predicted_x_px",
        "observed_x_px",
    ):
        assert rows[0][column] != ""


def test_gaia_sources_csv_has_projection_and_match_columns(run) -> None:
    result, paths = run
    rows = list(csv.DictReader(paths.sources.open()))
    assert len(rows) == len(result.query.rows)
    assert {"x_px", "y_px", "in_image", "eligible", "matched_detection_source_id"} <= set(rows[0])
    matched = [r for r in rows if r["matched_detection_source_id"]]
    assert len(matched) == result.summary.matches
    assert all(r["in_image"] == "true" and r["eligible"] == "true" for r in matched)


def test_summary_json(run) -> None:
    result, paths = run
    summary = _strict_json(paths.summary)
    assert summary["summary"]["matches"] == result.summary.matches
    assert summary["matching"]["match_radius_arcsec"] == 3.0
    assert summary["wcs_refinement"]["applied"] is True
    assert summary["inputs"] == {"image": "img.png"}
    assert "not object identification" in summary["statement"]
    assert summary["artifacts"]["overlay"] == "catalog_overlay.png"


def test_overlay_alignment(run) -> None:
    result, paths = run
    overlay = np.asarray(Image.open(paths.overlay).convert("RGB"))
    assert overlay.shape[1] == W and overlay.shape[0] > H  # legend strip below the image
    ring = max(6.0, 2.5 * result.match_radius_px)
    match = next(
        m for m in result.matches if 20 < m.observed_x_px < W - 20 and 20 < m.observed_y_px < H - 20
    )

    def coloured_near(x, y, color) -> bool:
        patch = overlay[round(y) - 1 : round(y) + 2, round(x) - 1 : round(x) + 2]
        return bool(np.any(np.all(patch == color, axis=-1)))

    assert coloured_near(match.observed_x_px - ring, match.observed_y_px, MATCH_COLOR)
    assert coloured_near(match.observed_x_px, match.observed_y_px + ring, MATCH_COLOR)
    assert (
        coloured_near(match.observed_x_px, match.observed_y_px, RESIDUAL_COLOR)
        or match.residual_px < 0.1
    )

from __future__ import annotations

import csv
import json

import numpy as np
from PIL import Image

from astroidentify.config import ObjectConfig
from astroidentify.objects.outputs import save_object_outputs
from astroidentify.objects.overlay import CATEGORY_COLORS, render_object_overlay
from astroidentify.objects.pipeline import identify_objects
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.types import ImageFormat
from tests.objects.fixtures import SCALE, FakeObjectProvider, H, W, row_at_pixel, wcs


def _image():
    return image_from_array(np.zeros((H, W), np.float32) + 10.0, image_format=ImageFormat.FITS,
                            display_origin="upper")  # fmt: skip


def _result(rows, w):
    return identify_objects(w, W, H, FakeObjectProvider(rows), ObjectConfig())


def test_overlay_keeps_dimensions_and_places_markers_exactly() -> None:
    w = wcs(rotation_deg=37.0)
    x0, y0 = 150, 90  # deliberately asymmetric position: a flip or offset would show
    result = _result([row_at_pixel(w, 1, x0, y0, "QSO")], w)
    overlay, report = render_object_overlay(_image(), result, max_labels=0)
    assert overlay.size == (W, H) and report.drawn == (1,) and report.labelled == ()
    window = np.asarray(overlay)[y0 - 40 : y0 + 41, x0 - 40 : x0 + 41]  # away from the legend
    colored = np.all(window == CATEGORY_COLORS["galaxy"], axis=2)
    ys, xs = np.nonzero(colored)
    xs, ys = xs + x0 - 40, ys + y0 - 40
    # The open crosshair is symmetric about the catalogue position.
    assert (xs.min() + xs.max()) / 2 == x0 and (ys.min() + ys.max()) / 2 == y0
    assert not colored[40, 40]  # open centre: the object itself stays visible


def test_overlay_ellipse_is_centred_on_the_object() -> None:
    w = wcs(rotation_deg=0.0)
    size = 2 * 30 * SCALE / 60  # 30 px radius
    row = dict(row_at_pixel(w, 1, 220, 140, "PN"), galdim_majaxis=size, galdim_minaxis=size,
               galdim_angle=0)  # fmt: skip
    overlay, _ = render_object_overlay(_image(), _result([row], w), max_labels=0)
    window = np.asarray(overlay)[80:200, 160:280]
    ys, xs = np.nonzero(np.all(window == CATEGORY_COLORS["planetary_nebula"], 2))
    xs, ys = xs + 160, ys + 80
    assert abs((xs.min() + xs.max()) / 2 - 220) <= 1 and abs((ys.min() + ys.max()) / 2 - 140) <= 1
    assert 58 <= xs.max() - xs.min() <= 64  # 30 px radius plus the stroke width


def test_outputs_are_written_and_consistent(tmp_path) -> None:
    w = wcs()
    rows = [row_at_pixel(w, 1, 100, 100, "PN", ids="NGC 1|NAME Test"),
            row_at_pixel(w, 2, 200, 100, "*")]  # fmt: skip
    result = _result(rows, w)
    config = ObjectConfig()
    paths, report = save_object_outputs(result, _image(), tmp_path / "objects", config,
                                        {"image": "synthetic"})  # fmt: skip
    names = {p.name for p in (tmp_path / "objects").iterdir()}
    assert names == {"object_query.json", "simbad_response.vot", "catalog_objects.csv",
                     "catalog_objects.json", "object_associations.csv",
                     "identification_summary.json", "object_overlay.png"}  # fmt: skip
    table = list(csv.DictReader(paths.objects_csv.open()))
    assert [r["catalogue_id"] for r in table] == ["1", "2"]
    assert table[0]["display_name"] == "NGC 1" and table[0]["common_names"] == "Test"
    assert table[1]["status"] == "in_field_type_excluded"
    associations = list(csv.DictReader(paths.associations.open()))
    assert [r["catalogue_id"] for r in associations] == ["1"]
    summary = json.loads(paths.summary.read_text())
    assert summary["counts"]["retained"] == 1 and summary["counts"]["rows_returned"] == 2
    assert summary["counts"]["annotated_on_overlay"] == 1 and summary["primary_object"] is None
    assert "confidence" not in json.dumps(summary["retained_objects"])
    assert json.loads(paths.query.read_text())["rows_returned"] == 2
    assert Image.open(paths.overlay).size == (W, H) and report.drawn == (1,)

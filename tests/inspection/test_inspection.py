"""Milestone 6.1: post-identification object inspection (offline)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astroidentify.astrometry.wcs import pixel_to_sky, sky_to_pixel
from astroidentify.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from astroidentify.inspection.pipeline import (
    InspectionOptions,
    choose_crop,
    extent_geometry,
)
from astroidentify.inspection.references import (
    GaiaCatalogue,
    select_reference_stars,
    star_name,
)
from astroidentify.inspection.render import View, orientation, render_view
from astroidentify.inspection.selection import ObjectSelectionError, normalise, select_object
from astroidentify.inspection.stretch import StretchParameters, stretch
from tests.astrometry.fixtures import make_truth_wcs

W, H = 400, 300


def record(oid, name, main=None, aliases=(), common=(), retained=True, category="nebula", **kw):
    return {
        "catalogue_id": oid, "display_name": name, "main_id": main or name,
        "aliases": list(aliases) or [name], "common_names": list(common),
        "object_type": "HII", "object_type_description": "HII Region", "category": category,
        "retained": retained, "status": "catalogued_in_field" if retained else
        "in_field_type_excluded", "exclusion_reason": None if retained else "category 'star'",
        **kw,
    }  # fmt: skip


# --------------------------------------------------------------------------- selection

TABLE = [
    record(1, "NGC 1", "NGC 1", ["NGC 1", "LBN 10", "SH 2-5", "NAME Test Cloud"], ["Test Cloud"]),
    record(2, "IC 2", "[XX] 2", ["IC 2", "[XX] 2", "SH 2-5"]),
    record(3, "BRIGHT", "* alf Tst", ["* alf Tst", "HD 1"], ["Brightstar"], retained=False,
           category="star"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("query", "oid", "method"),
    [("NGC 1", 1, "display_name"), ("[xx]  2", 2, "main_id"), ("lbn 10", 1, "alias"),
     ("  test   CLOUD ", 1, "alias")],
)  # fmt: skip
def test_selection_by_name_id_alias_with_harmless_normalization(query, oid, method) -> None:
    selection = select_object(TABLE, query)
    assert selection.record["catalogue_id"] == oid and selection.method == method
    assert normalise("  NGC   7000 ") == "ngc 7000"


def test_ambiguous_alias_fails_and_lists_candidates() -> None:
    with pytest.raises(ObjectSelectionError) as info:
        select_object(TABLE, "sh 2-5")
    assert "NGC 1" in str(info.value) and "IC 2" in str(info.value)


def test_unknown_and_out_of_field_objects_fail_clearly() -> None:
    with pytest.raises(ObjectSelectionError, match="no object in the image matches"):
        select_object(TABLE, "NGC 9999")
    outside = [*TABLE, {**record(4, "NGC 4", category="galaxy"), "retained": False,
                        "status": "outside_field"}]  # fmt: skip
    with pytest.raises(ObjectSelectionError, match="not in the image"):
        select_object(outside, "ngc 4")


def test_explicitly_requested_type_excluded_star_can_be_selected() -> None:
    selection = select_object(TABLE, "brightstar")  # a common name of an excluded star row
    assert selection.record["catalogue_id"] == 3 and selection.excluded_from_overlay
    assert selection.method == "alias" and selection.matched_text == "Brightstar"
    assert TABLE[2]["status"] == "in_field_type_excluded"  # the saved row is not modified
    assert not select_object(TABLE, "NGC 1").excluded_from_overlay


def test_retained_objects_take_precedence_over_excluded_rows() -> None:
    shared = [record(1, "NGC 1", aliases=["NGC 1", "HD 1"]), *TABLE[1:]]  # HD 1 on both rows
    selection = select_object(shared, "HD 1")
    assert selection.record["catalogue_id"] == 1 and not selection.excluded_from_overlay


def test_ambiguity_among_excluded_rows_fails() -> None:
    twins = [*TABLE, record(5, "OTHER", "* bet Tst", ["* bet Tst"], ["Brightstar"],
                            retained=False, category="star")]  # fmt: skip
    with pytest.raises(ObjectSelectionError, match="type-excluded"):
        select_object(twins, "brightstar")


def test_no_fuzzy_matching() -> None:
    with pytest.raises(ObjectSelectionError):
        select_object(TABLE, "NGC1")


# --------------------------------------------------------------------------- stretch


def _rgb(seed=0):
    rng = np.random.default_rng(seed)
    data = 10 + rng.normal(0, 2, (H, W, 3))
    data[100:110, 200:210] += np.array([200.0, 100.0, 50.0])  # an orange "star"
    return data.astype(np.float32)


@pytest.mark.parametrize("mode", ["none", "percentile", "asinh"])
def test_stretch_is_deterministic_and_keeps_geometry(mode) -> None:
    data = _rgb()
    a, record_a = stretch(data, StretchParameters(mode), nominal_max=255.0)
    b, record_b = stretch(data.copy(), StretchParameters(mode), nominal_max=255.0)
    assert a.shape == data.shape and a.dtype == np.uint8
    assert np.array_equal(a, b) and record_a == record_b and record_a["mode"] == mode


def test_none_stretch_shows_8bit_data_unchanged() -> None:
    data = np.arange(256, dtype=np.float32).reshape(16, 16)
    out, rec = stretch(data, StretchParameters("none"), nominal_max=255.0)
    assert np.array_equal(out, data.astype(np.uint8)) and rec["levels"]["white"] == 255.0


def test_percentile_and_asinh_levels_are_recorded_and_hue_is_kept() -> None:
    data = _rgb()
    params = StretchParameters("asinh", 10.0, 99.9, 0.05)
    out, rec = stretch(data, params)
    assert rec["levels"]["black"] == pytest.approx(np.percentile(data, 10))
    assert rec["softening"] == 0.05
    star = out[100:110, 200:210].reshape(-1, 3).astype(float).mean(axis=0)
    assert star[0] > star[1] > star[2]  # the orange star stays orange
    with pytest.raises(ValueError):
        StretchParameters("sharpen")


# --------------------------------------------------------------------------- geometry


def test_view_mapping_has_no_offset_and_zooms_pixel_centres() -> None:
    assert View(0, 0, 1).to_view(12.0, 7.0) == (12.0, 7.0)  # no hidden +1
    assert View(10, 5, 4).to_view(10.0, 5.0) == (1.5, 1.5)  # centre of the first 4x4 block
    assert View(10, 5, 4).to_view(11.0, 5.0)[0] == 5.5


def _wcs():
    return make_truth_wcs(W, H, centre=(150.0, 20.0), scale_arcsec=2.0, rotation_deg=30.0)


def _object_at(wcs, x, y, size_arcmin=None, **kw):
    ra, dec = pixel_to_sky(wcs, np.array([x]), np.array([y]))
    shape = "none" if size_arcmin is None else "ellipse"
    extent = {
        "major_arcmin": size_arcmin,
        "minor_arcmin": size_arcmin,
        "position_angle_deg": 0.0 if size_arcmin else None,
        "quality": None,
        "shape": shape,
    }
    return record(9, "OBJ 9", ra_deg=float(ra[0]), dec_deg=float(dec[0]), projected_x=float(x),
                  projected_y=float(y), extent=extent, centre_in_image=True,
                  footprint_fraction_in_image=1.0, **kw)  # fmt: skip


@pytest.mark.parametrize("zoom", [1, 3])
def test_marker_lands_on_the_projected_pixel(zoom) -> None:
    wcs = _wcs()
    obj = _object_at(wcs, 260, 190)
    x, y = sky_to_pixel(wcs, np.array([obj["ra_deg"]]), np.array([obj["dec_deg"]]))
    assert (float(x[0]), float(y[0])) == pytest.approx((260, 190))  # origin=0, no +1
    pixels = np.full((H, W, 3), 20, np.uint8)
    view = View(200, 150, zoom) if zoom > 1 else View(0, 0, 1)
    crop = pixels[150:, 200:] if zoom > 1 else pixels
    image = render_view(crop, view, wcs=wcs, obj=obj, outline=None, stars=[], label_stars=False,
                        pixel_scale_arcsec=2.0, notes=[])  # fmt: skip
    expected = view.to_view(260, 190)
    magenta = np.all(np.asarray(image) == (255, 60, 220), axis=2)
    cx, cy = round(expected[0]), round(expected[1])
    # Arms along the marker's own row and column (the label, also magenta, is elsewhere).
    row = np.flatnonzero(magenta[cy, cx - 60 : cx + 61]) - 60 + cx
    column = np.flatnonzero(magenta[cy - 60 : cy + 61, cx]) - 60 + cy
    assert (row.min() + row.max()) / 2 == pytest.approx(expected[0], abs=0.5)
    assert (column.min() + column.max()) / 2 == pytest.approx(expected[1], abs=0.5)
    assert not magenta[cy, cx]  # open centre
    if zoom == 1:
        assert image.size == (W, H)


def test_orientation_comes_from_the_wcs() -> None:
    wcs = make_truth_wcs(W, H, centre=(150.0, 20.0), scale_arcsec=2.0, rotation_deg=0.0)
    north, east = orientation(wcs, 200, 150)
    assert north == pytest.approx((0, -1), abs=1e-3)  # north up (row 0 at top)
    assert east == pytest.approx((-1, 0), abs=1e-3)  # east left for camera parity


def test_extent_projection_closed_clipped_containing_and_missing() -> None:
    wcs = _wcs()
    diameter = 2 * 30 * 2.0 / 60  # 30 px radius at 2"/px
    closed = extent_geometry(_object_at(wcs, 200, 150, diameter), wcs, W, H)
    xs, ys = np.array(closed.outline).T
    assert closed.available and closed.drawn == "closed outline" and not closed.extends_beyond_frame
    assert np.hypot(xs - 200, ys - 150) == pytest.approx(np.full(len(xs), 30.0), abs=0.05)

    clipped = extent_geometry(_object_at(wcs, 380, 150, diameter), wcs, W, H)
    assert clipped.extends_beyond_frame and clipped.drawn.startswith("visible arcs")

    huge = extent_geometry(_object_at(wcs, 200, 150, 120.0), wcs, W, H)
    assert huge.outline is None and huge.extends_beyond_frame and "not drawn" in huge.drawn

    none = extent_geometry(_object_at(wcs, 200, 150), wcs, W, H)
    assert not none.available and none.outline is None  # nothing invented


def test_crop_bounds() -> None:
    wcs = _wcs()
    options = InspectionOptions()
    obj = _object_at(wcs, 200, 150, 2 * 30 * 2.0 / 60)
    crop, reason = choose_crop(obj, extent_geometry(obj, wcs, W, H), W, H, options)
    assert crop == (155, 105, 245, 195) and "extent" in reason  # 1.5 x the 60 px extent
    point = _object_at(wcs, 390, 10)
    crop, reason = choose_crop(point, extent_geometry(point, wcs, W, H), W, H, options)
    assert crop == (300, 0, 400, 100) and "NOT a measured object boundary" in reason


# --------------------------------------------------------------------------- reference stars


def test_reference_stars_ranked_limited_and_named() -> None:
    gaia = GaiaCatalogue(
        source_id=np.array([5, 4, 3, 2, 1]), g_mag=np.array([9.0, 7.0, 7.0, 12.0, 8.0]),
        x=np.array([10.0, 20.0, 30.0, 40.0, 350.0]), y=np.array([10.0, 20.0, 30.0, 40.0, 280.0]),
    )  # fmt: skip
    simbad = [
        record(7, "x", aliases=["* bet Tst", "HD 5"], common=["Lantern"], category="star",
               projected_x=20.4, projected_y=20.0),
        record(8, "y", aliases=["TYC 1", "HD 77"], category="star", projected_x=30.0,
               projected_y=30.2),
    ]  # fmt: skip
    stars = select_reference_stars(gaia, 3, (0, 0, W, H), simbad, 1.0)
    assert [s.gaia_source_id for s in stars] == [3, 4, 1]  # G 7.0 (id 3), 7.0 (id 4), 8.0
    assert [s.name for s in stars] == ["HD 77", "Lantern", None]
    assert select_reference_stars(gaia, 10, (0, 0, 100, 100), [], 1.0)[-1].gaia_source_id == 2
    assert len(select_reference_stars(gaia, 10, (0, 0, W, H), [], 1.0, faintest_g=8.0)) == 3
    assert star_name(record(9, "z", aliases=["V* AB Tst", "HIP 3"])) == ("AB Tst", "V* AB Tst")


# --------------------------------------------------------------------------- end to end


@pytest.fixture
def products(saved_products, tmp_path, monkeypatch):
    from astroidentify import cli
    from tests.objects.fixtures import FakeObjectProvider, row_at_pixel

    p = saved_products
    astrometry = tmp_path / "astro"
    astrometry.mkdir()
    (astrometry / "plate_solution.json").write_text(p["plate"].read_text())
    (astrometry / "solution.wcs").write_bytes(p["wcs"].read_bytes())
    w = p["wcs_obj"]
    rows = [row_at_pixel(w, 1, 120, 70, "HII", ids="NGC 99|NAME Faint Cloud"),
            row_at_pixel(w, 2, 60, 50, "PN", ids="IC 5", galdim_majaxis=0.5, galdim_minaxis=0.5,
                         galdim_angle=0),
            # A named star: Milestone 5 excludes category "star" from its normal overlay.
            row_at_pixel(w, 3, 150, 110, "*", main_id="* gam Tst",
                         ids="* gam Tst|HD 4242|NAME Lanternstar|NAME Lanterne Star")]  # fmt: skip
    monkeypatch.setattr(cli, "SimbadProvider", lambda config: FakeObjectProvider(rows))
    objects = tmp_path / "objects"
    assert main(["identify", str(p["image"]), "--astrometry", str(astrometry), "-o",
                 str(objects), "--no-cache"]) == EXIT_OK  # fmt: skip
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    stars = [s for s in p["detection"].sources if s.accepted][:6]
    ra, dec = w.all_pix2world([s.x for s in stars], [s.y for s in stars], 0)
    with (catalog / "gaia_sources.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_id", "projected_ra_deg", "projected_dec_deg", "phot_g_mean_mag"])
        for k, (a, d) in enumerate(zip(ra, dec, strict=True)):
            writer.writerow([1000 + k, a, d, 8.0 + k])
    return {**p, "objects": objects, "catalog": catalog}


def _hashes(*dirs: Path) -> dict[str, str]:
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest()
            for d in dirs for f in sorted(d.rglob("*")) if f.is_file()}  # fmt: skip


def test_inspect_cli_end_to_end_without_upstream_mutation(products, tmp_path, capsys) -> None:
    p = products
    before = _hashes(p["objects"], p["catalog"], Path(p["image"]).parent / "det")
    out = tmp_path / "inspect"
    args = ["inspect-object", str(p["image"]), "--objects", str(p["objects"]), "--catalog",
            str(p["catalog"]), "--object", "faint  cloud", "-o", str(out),
            "--reference-stars", "4"]  # fmt: skip
    assert main(args) == EXIT_OK
    stdout = capsys.readouterr().out
    assert "Selected: NGC 99" in stdout and "by alias" in stdout and "display only" in stdout
    assert _hashes(p["objects"], p["catalog"], Path(p["image"]).parent / "det") == before
    summary = json.loads((out / "inspection_summary.json").read_text())
    assert summary["selection"]["method"] == "alias"
    assert summary["object"]["catalogue_id"] == 1 and summary["extent"]["extent_available"] is False
    assert any("no visual boundary" in w for w in summary["warnings"])
    assert summary["stretch"]["mode"] == "asinh" and summary["stretch"]["display_only"]
    assert len(summary["reference_stars"]["full"]) == 4
    assert [s["g_mag"] for s in summary["reference_stars"]["full"]] == [8.0, 9.0, 10.0, 11.0]
    assert summary["object"]["reprojection_difference_px"] < 1e-6
    crop = summary["crop"]
    assert 0 <= crop["x0"] < crop["x1"] <= 200 and 0 <= crop["y0"] < crop["y1"] <= 160
    full = Image.open(out / "inspection_full.png")
    assert full.size == (200, 160)  # the image's own dimensions
    zoom = Image.open(out / "inspection_zoom.png")
    side = crop["x1"] - crop["x0"]
    assert zoom.size == (side * crop["zoom_factor"], side * crop["zoom_factor"])

    sized = tmp_path / "sized"
    assert main([*args[:-6], "--object", "IC 5", "-o", str(sized), "--stretch", "percentile"]) == 0
    s2 = json.loads((sized / "inspection_summary.json").read_text())
    assert s2["extent"]["extent_available"] and s2["extent"]["drawn"] == "closed outline"
    assert s2["stretch"]["mode"] == "percentile"


def test_inspect_cli_failures(products, tmp_path, capsys) -> None:
    p = products
    base = ["inspect-object", str(p["image"]), "--objects", str(p["objects"])]
    assert main([*base, "--object", "NGC 12345", "-o", str(tmp_path / "x")]) == EXIT_ERROR
    assert "no object in the image matches" in capsys.readouterr().err
    assert main([*base, "--object", "IC 5", "-o", str(p["objects"])]) == EXIT_ERROR
    assert "must differ" in capsys.readouterr().err
    assert main([*base, "--object", "IC 5", "--low-percentile", "99", "--high-percentile",
                 "1"]) == EXIT_USAGE  # fmt: skip


def test_scientific_modules_never_import_the_inspection_package() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "astroidentify"
    for package in ("preprocessing", "detection", "astrometry", "catalogs", "objects", "evidence"):
        for source in (root / package).rglob("*.py"):
            assert "astroidentify.inspection" not in source.read_text(), source


def test_name_matching_moves_gaia_stars_to_the_simbad_epoch(tmp_path) -> None:
    from astroidentify.inspection.references import load_gaia

    wcs = make_truth_wcs(W, H, centre=(150.0, 20.0), scale_arcsec=2.0, rotation_deg=0.0)
    ra, dec = pixel_to_sky(wcs, np.array([200.0]), np.array([150.0]))
    path = tmp_path / "gaia_sources.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_id", "projected_ra_deg", "projected_dec_deg", "phot_g_mean_mag",
                         "pmra", "pmdec", "ref_epoch"])  # fmt: skip
        writer.writerow([1, ra[0], dec[0], 6.0, 1000.0, 0.0, 2016.0])  # 1"/yr toward east
    gaia = load_gaia(path, wcs, W, H)
    assert (gaia.x[0], gaia.y[0]) == pytest.approx((200.0, 150.0))  # displayed: Gaia epoch
    # J2000 is 16" further west; west is +x for this camera-parity WCS (2"/px -> 8 px).
    assert gaia.x_name[0] - gaia.x[0] == pytest.approx(8.0, abs=0.01)
    assert gaia.y_name[0] == pytest.approx(150.0, abs=0.01)


# --------------------------------------------------------------------------- label


def test_label_prefers_a_common_name_and_keeps_the_designation_second() -> None:
    from astroidentify.inspection.render import object_label, preferred_common_name

    names = ["FOO BAR NEB", "Foo Bar", "Foo Bar Nebula", "Baz Cluster"]  # invented, SIMBAD-like
    assert preferred_common_name(names) == "Foo Bar Nebula"  # mixed case, most complete
    obj = record(5, "NGC 5", "NGC 5", common=names, object_type_description="Cluster of Stars")
    assert object_label(obj) == {"primary": "Foo Bar Nebula", "secondary": "NGC 5",
                                 "source": "simbad_common_name"}  # fmt: skip
    other_main = record(6, "M 6", "NGC 6", common=["Sixth Object"])
    assert object_label(other_main)["secondary"] == "M 6 [NGC 6]"
    assert preferred_common_name(["ONLY CAPS"]) == "ONLY CAPS"  # still a name, used if alone


def test_label_falls_back_to_designation_and_type_without_common_names() -> None:
    from astroidentify.inspection.render import object_label

    obj = record(7, "IC 7", "[XY] 7", object_type_description="HII Region")
    assert object_label(obj) == {"primary": "IC 7 [[XY] 7]", "secondary": "HII Region",
                                 "source": "display_name"}  # fmt: skip
    assert object_label(record(8, "IC 8", common=["", "  "]))["source"] == "display_name"


def test_summary_records_label_and_keeps_identity(products, tmp_path) -> None:
    p = products
    out = tmp_path / "label"
    args = ["inspect-object", str(p["image"]), "--objects", str(p["objects"]), "--catalog",
            str(p["catalog"]), "--object", "NGC 99", "-o", str(out)]  # fmt: skip
    assert main(args) == EXIT_OK
    summary = json.loads((out / "inspection_summary.json").read_text())
    # The synthetic row's main ID ("OBJ 1") differs from its display name, so both are shown.
    assert summary["label"]["primary"] == "Faint Cloud"
    assert summary["label"]["secondary"] == "NGC 99 [OBJ 1]"
    assert summary["object"]["display_name"] == "NGC 99"  # stored identity unchanged
    assert summary["object"]["object_type"] == "HII"  # SIMBAD type kept in metadata


def test_inspect_cli_explicit_excluded_star(products, tmp_path, capsys) -> None:
    p = products
    m5 = json.loads((p["objects"] / "catalog_objects.json").read_text())["objects"]
    star = next(o for o in m5 if o["catalogue_id"] == 3)
    assert star["status"] == "in_field_type_excluded" and star["category"] == "star"
    before = _hashes(p["objects"], p["catalog"])
    out = tmp_path / "star"
    args = ["inspect-object", str(p["image"]), "--objects", str(p["objects"]), "--catalog",
            str(p["catalog"]), "--object", "lanternstar", "-o", str(out)]  # fmt: skip
    assert main(args) == EXIT_OK
    stdout = capsys.readouterr().out
    assert "excluded from the normal Milestone 5 overlay" in stdout
    summary = json.loads((out / "inspection_summary.json").read_text())
    selection = summary["selection"]
    assert selection["excluded_from_normal_overlay"] is True
    assert selection["explicitly_selected_for_inspection"] is True
    assert selection["milestone5_status"] == "in_field_type_excluded"
    assert "star" in selection["milestone5_exclusion_reason"]
    assert summary["object"]["catalogue_id"] == 3 and summary["object"]["category"] == "star"
    assert summary["label"]["primary"] == "Lanternstar"  # the requested spelling of the name
    # Milestone 5 artifacts (tables, summary, normal overlay) are byte-for-byte unchanged.
    assert _hashes(p["objects"], p["catalog"]) == before
    retained = json.loads((p["objects"] / "identification_summary.json").read_text())
    assert 3 not in {o["catalogue_id"] for o in retained["retained_objects"]}

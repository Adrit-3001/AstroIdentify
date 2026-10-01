from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest
from astropy.io import fits

from astroidentify.config import ObjectConfig
from astroidentify.exceptions import (
    ConfigurationError,
    InputMismatchError,
    InvalidPlateSolutionError,
)
from astroidentify.objects.pipeline import identify_objects, load_identify_inputs, select_wcs
from astroidentify.objects.types import (
    ASSOC_EXTENDED,
    ASSOC_NO_POINT_SOURCE,
    ASSOC_NOT_ATTEMPTED,
    ASSOC_POINT_SOURCE,
    FIELD_CENTRE_IN_IMAGE,
    FIELD_EXTENT_OVERLAPS,
    STATUS_IN_FIELD,
    STATUS_OUTSIDE,
    STATUS_TYPE_EXCLUDED,
)
from tests.catalogs.fixtures import sources_at
from tests.objects.fixtures import SCALE, FakeObjectProvider, H, W, row_at_pixel, wcs


def _identify(rows, w=None, **kwargs):
    w = w or wcs()
    config = kwargs.pop("config", ObjectConfig())
    return identify_objects(w, W, H, FakeObjectProvider(rows), config, **kwargs)


def _by_id(result):
    return {o.catalogue_id: o for o in result.objects}


@pytest.mark.parametrize("rotation", [0.0, 30.0, 200.0])
def test_projection_is_canonical_with_no_flip_or_offset(rotation) -> None:
    w = wcs(rotation_deg=rotation)
    rows = [row_at_pixel(w, 1, 0.0, 0.0), row_at_pixel(w, 2, 399.0, 299.0),
            row_at_pixel(w, 3, 123.25, 45.75)]  # fmt: skip
    objects = _by_id(_identify(rows, w))
    for oid, (x, y) in {1: (0.0, 0.0), 2: (399.0, 299.0), 3: (123.25, 45.75)}.items():
        assert objects[oid].projected_x == pytest.approx(x, abs=1e-6)
        assert objects[oid].projected_y == pytest.approx(y, abs=1e-6)
        assert objects[oid].centre_in_image


@pytest.mark.parametrize(
    "centre", [(0.01, 10.0), (359.99, -5.0), (75.0, 88.9), (210.0, -89.5)]
)  # RA wrap and high declination
def test_query_region_covers_all_corners_near_wrap_and_poles(centre) -> None:
    w = wcs(centre=centre)
    corners = [(-0.5, -0.5), (W - 0.5, -0.5), (-0.5, H - 0.5), (W - 0.5, H - 0.5)]
    rows = [row_at_pixel(w, k, x * 0.999, y * 0.999) for k, (x, y) in enumerate(corners, 1)]
    provider = FakeObjectProvider(rows)
    result = identify_objects(w, W, H, provider, ObjectConfig())
    region = provider.regions[0]
    from astropy.coordinates import SkyCoord

    c = SkyCoord(region.centre.ra_deg, region.centre.dec_deg, unit="deg")
    for r in rows:
        assert c.separation(SkyCoord(r["ra"], r["dec"], unit="deg")).deg < region.radius_deg
    assert all(o.centre_in_image for o in result.objects)


def test_type_policy_status_and_raw_rows_preserved() -> None:
    w = wcs()
    rows = [
        row_at_pixel(w, 1, 100, 100, "PN"), row_at_pixel(w, 2, 200, 100, "G"),
        row_at_pixel(w, 3, 300, 100, "OpC"), row_at_pixel(w, 4, 100, 200, "*"),
        row_at_pixel(w, 5, 200, 200, "Rad"), row_at_pixel(w, 6, 900, 900, "G"),  # outside
    ]  # fmt: skip
    objects = _by_id(_identify(rows, w))
    assert len(objects) == 6  # every returned row is kept
    assert {k for k, o in objects.items() if o.status == STATUS_IN_FIELD} == {1, 2, 3}
    assert objects[4].status == STATUS_TYPE_EXCLUDED and "star" in objects[4].exclusion_reason
    assert objects[5].status == STATUS_TYPE_EXCLUDED
    assert objects[6].status == STATUS_OUTSIDE and not objects[6].centre_in_image


def test_extended_object_centred_outside_is_in_field() -> None:
    w = wcs()
    ra_dec = row_at_pixel(w, 1, W - 0.5 + 40, 150, "HII")  # 40 px = 80" beyond the edge
    big = dict(ra_dec, galdim_majaxis=2 * 120 / 60, galdim_minaxis=2 * 120 / 60, galdim_angle=0)
    (obj,) = _identify([big], w).objects
    assert obj.field_status == FIELD_EXTENT_OVERLAPS and obj.status == STATUS_IN_FIELD
    assert 0 < obj.footprint_fraction_in_image < 0.5 and not obj.centre_in_image


def test_compact_association_nearby_outside_and_ties() -> None:
    w = wcs()
    rows = [row_at_pixel(w, 1, 100, 100, "QSO"), row_at_pixel(w, 2, 250, 150, "QSO"),
            row_at_pixel(w, 3, 50, 250, "QSO")]  # fmt: skip
    radius_px = 3.0 / SCALE  # default 3" association radius
    detections = sources_at(np.array([
        [100.6, 100.0],  # 0.6 px from object 1
        [250.0 + radius_px + 0.2, 150.0],  # just outside the radius of object 2
        [50.5, 250.0], [49.5, 250.0],  # exact tie for object 3
    ]))  # fmt: skip
    objects = _by_id(_identify(rows, w, detections=detections))
    a1 = objects[1].association
    assert a1.kind == ASSOC_POINT_SOURCE and a1.detection_source_id == 1
    assert a1.separation_px == pytest.approx(0.6) and a1.separation_arcsec == pytest.approx(1.2)
    assert objects[2].association.kind == ASSOC_NO_POINT_SOURCE
    a3 = objects[3].association
    assert a3.detection_source_id == 3 and a3.n_detections_within_radius == 2  # lower ID wins


def test_association_uses_astrometric_centroids() -> None:
    w = wcs()
    (det,) = sources_at(np.array([[120.0, 80.0]]))
    det = dataclasses.replace(det, astrometric_x=100.2, astrometric_y=100.0)
    result = _identify([row_at_pixel(w, 1, 100, 100, "QSO")], w, detections=[det])
    assert result.objects[0].association.separation_px == pytest.approx(0.2)


def test_extended_object_without_point_detection_gets_footprint_evidence() -> None:
    w = wcs(rotation_deg=0.0)
    plane = np.zeros((H, W), np.float32) + np.random.default_rng(1).normal(0, 1, (H, W))
    yy, xx = np.mgrid[:H, :W]
    plane[np.hypot(xx - 200, yy - 150) <= 15] += 20.0  # a faint blob, no detection
    diameter = 2 * 15 * SCALE / 60
    extended = dict(row_at_pixel(w, 1, 200, 150, "PN"), galdim_majaxis=diameter,
                    galdim_minaxis=diameter, galdim_angle=0)  # fmt: skip
    result = _identify([extended], w, detections=[], plane=plane)
    (obj,) = result.objects
    a = obj.association
    assert obj.status == STATUS_IN_FIELD and obj.field_status == FIELD_CENTRE_IN_IMAGE
    assert a.kind == ASSOC_EXTENDED and a.detections_in_footprint == 0
    assert a.brightness_contrast > 10 and a.detection_source_id is None


def test_without_detections_association_is_not_attempted() -> None:
    result = _identify([row_at_pixel(wcs(), 1, 100, 100, "QSO")])
    assert result.objects[0].association.kind == ASSOC_NOT_ATTEMPTED
    assert any("not attempted" in w for w in result.warnings)


def test_valid_empty_field_and_deterministic_order() -> None:
    empty = _identify([])
    assert empty.objects == () and "no objects" in empty.warnings[0]
    w = wcs()
    rows = [row_at_pixel(w, 5, 10, 10, "G", ids="UGC 5"), row_at_pixel(w, 4, 20, 20, "G",
            ids="NGC 4"), row_at_pixel(w, 6, 30, 30, "G", ids="M 6")]  # fmt: skip
    a = [o.display_name for o in _identify(rows, w).objects]
    b = [o.display_name for o in _identify(rows[::-1], w).objects]
    assert a == b == ["M 6", "NGC 4", "UGC 5"]


def test_unknown_category_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        _identify([], config=ObjectConfig(included_categories=("comets",)))


# --------------------------------------------------------------------------- inputs


def _products(tmp_path, saved_products, refined=True):
    p = saved_products
    astrometry = tmp_path / "astro"
    astrometry.mkdir()
    (astrometry / "plate_solution.json").write_text(p["plate"].read_text())
    (astrometry / "solution.wcs").write_bytes(p["wcs"].read_bytes())
    catalog = tmp_path / "cat"
    catalog.mkdir()
    image_sha = json.loads(p["plate"].read_text())["input"]["source_sha256"]
    summary = {"inputs": {"image_sha256": image_sha, "detections": str(p["sources"])}}
    (catalog / "catalog_match_summary.json").write_text(json.dumps(summary))
    if refined:
        (catalog / "refined_solution.wcs").write_bytes(p["wcs"].read_bytes())
    return astrometry, catalog


def test_wcs_selection_prefers_refined_then_plate_solution(tmp_path, saved_products) -> None:
    astrometry, catalog = _products(tmp_path, saved_products)
    assert select_wcs(astrometry, catalog, None)[1].source == "catalog_refined"
    (catalog / "refined_solution.wcs").unlink()
    choice = select_wcs(astrometry, catalog, None)[1]
    assert choice.source == "plate_solution" and choice.refined is False
    assert select_wcs(None, None, astrometry / "solution.wcs")[1].source == "explicit"
    with pytest.raises(InvalidPlateSolutionError):
        select_wcs(None, tmp_path / "nothing", None)


def test_load_inputs_and_consistency_checks(tmp_path, saved_products) -> None:
    astrometry, catalog = _products(tmp_path, saved_products)
    image = saved_products["image"]
    inputs = load_identify_inputs(image, astrometry_dir=astrometry, catalog_dir=catalog)
    assert inputs.wcs_choice.refined and inputs.detections  # detections found via catalog run
    assert inputs.plane.shape == (inputs.height, inputs.width)

    summary = json.loads((catalog / "catalog_match_summary.json").read_text())
    summary["inputs"]["image_sha256"] = "0" * 64
    (catalog / "catalog_match_summary.json").write_text(json.dumps(summary))
    with pytest.raises(InputMismatchError):
        load_identify_inputs(image, astrometry_dir=astrometry, catalog_dir=catalog)

    header = fits.getheader(astrometry / "solution.wcs")
    header["IMAGEW"] = 999
    fits.PrimaryHDU(header=header).writeto(tmp_path / "bad.wcs")
    with pytest.raises(InputMismatchError):
        load_identify_inputs(image, wcs_path=tmp_path / "bad.wcs")

    plate = json.loads((astrometry / "plate_solution.json").read_text())
    plate["solved"] = False
    (astrometry / "plate_solution.json").write_text(json.dumps(plate))
    with pytest.raises(InvalidPlateSolutionError):
        load_identify_inputs(image, astrometry_dir=astrometry)

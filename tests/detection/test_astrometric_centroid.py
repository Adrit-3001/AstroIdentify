"""Milestone 4.1: isophote-calibrated astrometric centroids for saturated stars (offline)."""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest
from astropy.io import fits

from astroidentify.astrometry.outputs import SELECTED_COLUMNS, selection_to_csv
from astroidentify.astrometry.selection import select_from_sources
from astroidentify.astrometry.types import TIER_PREFERRED, TIER_SATURATED
from astroidentify.astrometry.xylist import write_xylist
from astroidentify.catalogs.pipeline import match_catalog
from astroidentify.config import CatalogConfig, DetectionConfig
from astroidentify.detection import detect_sources
from astroidentify.detection.astrometric_centroid import (
    ASTROMETRIC_DETECTION,
    ASTROMETRIC_FALLBACK,
    ASTROMETRIC_ISOPHOTE,
    apply_astrometric_centroids,
    build_isophote_calibration,
)
from astroidentify.detection.outputs import load_sources, sources_document, sources_to_csv
from astroidentify.detection.saturation import CENTROID_SATURATED_CORE
from astroidentify.detection.types import Source
from astroidentify.types import ImageFormat
from tests.catalogs.fixtures import FakeProvider, H, W, sources_at, synthetic_field
from tests.detection.synthetic import FWHM_TO_SIGMA, as_preprocessed, render_field

FWHM = 4.0
SHAPE = (400, 400)
CONFIG = DetectionConfig(isophote_calibration_min_stars=10)
# Comet-like PSF: a Gaussian core plus a fainter, broader lobe displaced up-left (the
# benchmark's tail direction). ``TAIL = None`` gives a symmetric Gaussian.
TAIL = (0.35, 4.0, (-3.0, -2.5))  # relative amplitude, sigma (px), lobe offset (dx, dy)


def _psf(x0: float, y0: float, amplitude: float, tail=TAIL) -> np.ndarray:
    rows, cols = np.mgrid[: SHAPE[0], : SHAPE[1]].astype(float)
    sigma = FWHM * FWHM_TO_SIGMA
    image = amplitude * np.exp(-((cols - x0) ** 2 + (rows - y0) ** 2) / (2 * sigma**2))
    if tail is not None:
        ratio, tail_sigma, (dx, dy) = tail
        r2 = (cols - x0 - dx) ** 2 + (rows - y0 - dy) ** 2
        image += ratio * amplitude * np.exp(-r2 / (2 * tail_sigma**2))
    return image


def _calibration_stars() -> list[tuple[float, float]]:
    """A 4 x 4 grid of isolated stars with varied sub-pixel phases."""
    return [
        (50 + 90 * i + (0.13 * (4 * i + j)) % 1, 50 + 90 * j + (0.37 * (4 * i + j)) % 1)
        for i in range(4)
        for j in range(4)
    ]


def _field(tail=TAIL, saturated_star=(95.4, 95.7), saturated_amplitude=40.0):
    """Noise-free background-subtracted plane, saturation mask and ``Source`` list.

    Calibration-star sources sit at their true model positions (a perfect detection
    centroid); the saturated source sits at its plateau centroid, as Milestone 2 does.
    """
    plane = np.zeros(SHAPE)
    sources = []
    for k, (x, y) in enumerate(_calibration_stars(), start=2):
        plane += _psf(x, y, 0.5, tail)
        sources.append(_source(k, x, y, flux=100.0 - k))
    plane += _psf(*saturated_star, saturated_amplitude, tail)
    saturated = plane >= 1.0  # the clipping level
    rows, cols = np.nonzero(saturated)
    core = Source(**{**_source(1, 0, 0, 1e4).__dict__, "x": float(cols.mean()),
                     "y": float(rows.mean()), "saturated": True,
                     "centroid_method": CENTROID_SATURATED_CORE,
                     "saturated_core_area": int(saturated.sum()),
                     "n_saturated_pixels": int(saturated.sum())})  # fmt: skip
    return np.minimum(plane, 1.0), saturated, [core, *sources]


def _source(source_id: int, x: float, y: float, flux: float) -> Source:
    nan = float("nan")
    return Source(
        source_id=source_id, x=x, y=y, flux=flux, flux_err=1.0, snr=flux, peak=1.0,
        fwhm=FWHM, sharpness=0.5, roundness1=0.0, roundness2=0.0, local_background=0.0,
        local_rms=0.01, edge_distance=50.0, n_saturated_pixels=0, saturated=False,
        edge=False, saturated_core_axis_ratio=nan,
    )  # fmt: skip


def _astrometric(tail=TAIL, **kwargs):
    plane, saturated, sources = _field(tail, **kwargs)
    calibration, note = build_isophote_calibration(sources, plane, saturated, FWHM, CONFIG)
    return calibration, note, apply_astrometric_centroids(sources, calibration)


def test_comet_like_saturated_star_is_corrected_toward_the_true_position() -> None:
    truth = (95.4, 95.7)
    calibration, _, sources = _astrometric(TAIL, saturated_star=truth)
    assert calibration is not None and calibration.n_stars == 16
    star = sources[0]
    core_error = np.hypot(star.x - truth[0], star.y - truth[1])
    corrected_error = np.hypot(star.astrometric_x - truth[0], star.astrometric_y - truth[1])
    assert star.astrometric_method == ASTROMETRIC_ISOPHOTE
    assert core_error > 1.0  # the plateau centroid is pulled toward the tail
    assert corrected_error < 0.3 and corrected_error < core_error / 4
    # The correction moves the position away from the tail (+x, +y here): no flip.
    assert star.astrometric_x > star.x and star.astrometric_y > star.y
    assert star.astrometric_correction_px == pytest.approx(
        np.hypot(star.astrometric_x - star.x, star.astrometric_y - star.y)
    )


def test_symmetric_clipped_star_gets_almost_no_correction() -> None:
    truth = (95.4, 95.7)
    _, _, sources = _astrometric(None, saturated_star=truth)
    star = sources[0]
    assert star.astrometric_method == ASTROMETRIC_ISOPHOTE
    assert star.astrometric_correction_px < 0.15
    # Canonical origin preserved: no half-pixel or one-pixel shift appears.
    assert np.hypot(star.astrometric_x - truth[0], star.astrometric_y - truth[1]) < 0.15


def test_unsaturated_sources_are_unchanged() -> None:
    _, _, sources = _astrometric()
    for source in sources[1:]:
        assert source.astrometric_method == ASTROMETRIC_DETECTION
        assert source.astrometric_xy == (source.x, source.y)
        assert source.astrometric_correction_px == 0.0


def test_core_beyond_the_calibrated_range_falls_back_deterministically() -> None:
    calibration, _, _ = _astrometric()
    _, _, sources = _astrometric()
    huge = dataclasses.replace(sources[0], saturated_core_area=int(calibration.max_area) + 1)
    first = apply_astrometric_centroids([huge], calibration)[0]
    second = apply_astrometric_centroids([huge], calibration)[0]
    assert first.astrometric_method == ASTROMETRIC_FALLBACK
    assert first.astrometric_xy == (huge.x, huge.y) and first == second


def test_too_few_calibration_stars_falls_back_with_a_reason() -> None:
    plane, saturated, sources = _field()
    calibration, note = build_isophote_calibration(sources[:5], plane, saturated, FWHM, CONFIG)
    assert calibration is None and "only 4" in note
    star = apply_astrometric_centroids(sources, calibration)[0]
    assert star.astrometric_method == ASTROMETRIC_FALLBACK
    assert star.astrometric_xy == (star.x, star.y)


def test_calibration_is_deterministic_and_order_independent() -> None:
    plane, saturated, sources = _field()
    a, _ = build_isophote_calibration(sources, plane, saturated, FWHM, CONFIG)
    b, _ = build_isophote_calibration(sources[::-1], plane, saturated, FWHM, CONFIG)
    np.testing.assert_array_equal(a.area, b.area)
    np.testing.assert_array_equal(a.dx, b.dx)
    np.testing.assert_array_equal(a.dy, b.dy)


def test_replacing_x_y_never_leaves_a_stale_astrometric_position() -> None:
    source = _source(1, 10.0, 20.0, 5.0)
    moved = dataclasses.replace(source, x=11.0)
    assert moved.astrometric_xy == (11.0, 20.0)


def test_serialization_round_trip(tmp_path) -> None:
    _, _, sources = _astrometric()
    document = {"sources": [s.to_dict() for s in sources]}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(document))
    loaded = load_sources(path)
    assert [s.astrometric_xy for s in loaded] == [s.astrometric_xy for s in sources]
    assert [s.astrometric_method for s in loaded] == [s.astrometric_method for s in sources]
    assert loaded[0].astrometric_correction_px == sources[0].astrometric_correction_px
    # Detection centroids and IDs are untouched.
    assert [(s.source_id, s.x, s.y) for s in loaded] == [(s.source_id, s.x, s.y) for s in sources]
    header, *rows = (line.split(",") for line in sources_to_csv(sources).splitlines())
    ax, ay = header.index("astrometric_x"), header.index("astrometric_y")
    # Unset (same-as-detection) values are written resolved, never as empty/None.
    written = [(float(r[ax]), float(r[ay])) for r in rows]
    np.testing.assert_allclose(written, [s.astrometric_xy for s in sources], atol=1e-6)


def test_files_written_before_the_astrometric_fields_still_load(tmp_path) -> None:
    record = _source(3, 12.5, 7.25, 9.0).to_dict()
    for name in ("astrometric_x", "astrometric_y", "astrometric_method",
                 "astrometric_correction_px"):  # fmt: skip
        del record[name]
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"sources": [record]}))
    (source,) = load_sources(path)
    assert source.astrometric_xy == (12.5, 7.25) and source.astrometric_method == "detection"


def test_plate_solver_source_list_uses_the_astrometric_centroid(tmp_path) -> None:
    sources = [
        dataclasses.replace(_source(1, 100.0, 80.0, 1000.0), saturated=True,
                            astrometric_x=102.5, astrometric_y=83.0,
                            astrometric_method=ASTROMETRIC_ISOPHOTE),
        *(_source(k, 30.0 * k, 20.0 * k + 3, 100.0 / k) for k in range(2, 6)),
    ]  # fmt: skip
    selection = select_from_sources(
        sources, width=200, height=200, max_sources=5, min_sources=3,
        allowed_tiers=(TIER_PREFERRED, TIER_SATURATED), pooled=True,
    )  # fmt: skip
    first = selection.sources[0]
    assert (first.source_id, first.x, first.y) == (1, 102.5, 83.0)
    assert first.astrometric_method == ASTROMETRIC_ISOPHOTE
    with fits.open(write_xylist(selection, tmp_path / "s.xyls")) as hdus:
        table = hdus[1].data
        assert (table["X"][0], table["Y"][0]) == (103.5, 84.0)  # canonical + 1
        assert table["X"][1] == sources[1].x + 1  # unsaturated: detection centroid + 1
    assert SELECTED_COLUMNS[-1] == "astrometric_method"
    assert selection_to_csv(selection).splitlines()[1].endswith(ASTROMETRIC_ISOPHOTE)
    assert sources[0].x == 100.0  # the canonical detection centroid is not mutated


def test_catalogue_matching_uses_the_astrometric_centroid() -> None:
    field = synthetic_field()
    exact = sources_at(field.star_xy)
    # Detection centroids 10 px off (beyond the 3" radius); astrometric centroids exact.
    sources = [
        dataclasses.replace(s, x=s.x + 10, astrometric_x=s.x, astrometric_y=s.y,
                            astrometric_method=ASTROMETRIC_ISOPHOTE)
        for s in exact
    ]  # fmt: skip
    result = match_catalog(
        sources, field.wcs, W, H, FakeProvider(field.rows), CatalogConfig(refine_wcs=False)
    )
    assert result.summary.matches == len(sources)
    by_id = {s.source_id: s for s in sources}
    for match in result.matches:
        assert match.observed_x_px == by_id[match.detection_source_id].astrometric_x


def test_detection_pipeline_records_astrometric_centroids() -> None:
    """End to end on a clipped raster: saturated cores corrected, everything else untouched."""
    stars = [(x, y) for x in range(30, 380, 45) for y in range(30, 380, 45)]
    data = sum((_psf(x + 0.3, y + 0.6, 120.0) for x, y in stars), np.zeros(SHAPE))
    data += _psf(212.4, 207.9, 4000.0)  # one heavily saturated comet-like star
    image = np.clip(render_field(SHAPE, background=20.0, noise=2.0, seed=3) + data, 0, 255)
    detection = detect_sources(
        as_preprocessed(image, image_format=ImageFormat.PNG, nominal_max=255.0), CONFIG
    )
    summary = detection.diagnostics["astrometric_centroid"]
    assert summary["enabled"] and summary["calibration"] is not None
    accepted = detection.accepted_sources
    core = [s for s in accepted if s.centroid_method == CENTROID_SATURATED_CORE]
    assert len(core) == 1 and core[0].astrometric_method == ASTROMETRIC_ISOPHOTE
    assert np.hypot(core[0].astrometric_x - 212.4, core[0].astrometric_y - 207.9) < np.hypot(
        core[0].x - 212.4, core[0].y - 207.9
    )
    for source in accepted:
        if source is not core[0]:
            assert source.astrometric_xy == (source.x, source.y)
    # The solver input follows the astrometric centroid; metadata documents the fields.
    assert tuple(detection.xy_flux()[0, :2]) == core[0].astrometric_xy
    assert "astrometric_x" in sources_document(detection)["fields"]

    disabled = detect_sources(
        as_preprocessed(image, image_format=ImageFormat.PNG, nominal_max=255.0),
        dataclasses.replace(CONFIG, astrometric_centroid=False),
    )
    assert all(s.astrometric_xy == (s.x, s.y) for s in disabled.sources)
    assert {s.astrometric_method for s in disabled.sources} <= {
        ASTROMETRIC_DETECTION,
        ASTROMETRIC_FALLBACK,
    }

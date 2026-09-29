"""End-to-end detection on synthetic star fields with known positions."""

from __future__ import annotations

import numpy as np
import pytest

from astroidentify.config import DetectionConfig
from astroidentify.detection import detect_sources
from astroidentify.detection.types import COORDINATE_CONVENTION
from astroidentify.types import ImageFormat
from tests.detection.synthetic import Star, as_preprocessed, grid_stars, nearest, render_field

SHAPE = (200, 240)


def _detect(data: np.ndarray, config: DetectionConfig | None = None, **kwargs):
    return detect_sources(as_preprocessed(data, **kwargs), config)


def test_isolated_stars_recovered_with_subpixel_accuracy() -> None:
    stars = grid_stars(SHAPE, spacing=40, amplitude=800.0, fwhm=4.0)
    result = _detect(render_field(SHAPE, stars=stars))

    assert len(result.accepted_sources) >= len(stars)
    for star in stars:
        source, distance = nearest(result.accepted_sources, star.x, star.y)
        assert distance < 0.15, (star, source)


def test_fwhm_is_estimated() -> None:
    stars = grid_stars(SHAPE, spacing=40, amplitude=800.0, fwhm=5.0)
    result = _detect(render_field(SHAPE, stars=stars))
    assert result.fwhm.method == "estimated"
    assert result.fwhm.value == pytest.approx(5.0, rel=0.1)
    assert result.fwhm.n_stars >= 5


def test_configured_fwhm_is_used() -> None:
    stars = grid_stars(SHAPE, spacing=40, amplitude=800.0, fwhm=4.0)
    result = _detect(render_field(SHAPE, stars=stars), DetectionConfig(fwhm=4.5))
    assert result.fwhm.method == "configured" and result.fwhm.value == 4.5


def test_coordinate_convention_x_is_column_y_is_row() -> None:
    # One star at row 40 (near the top), column 170 (right side), on an asymmetric image.
    result = _detect(render_field((120, 200), stars=[Star(170.0, 40.0, 1500.0, 4.0)]))
    source = result.brightest(1)[0]
    assert source.x == pytest.approx(170.0, abs=0.15)
    assert source.y == pytest.approx(40.0, abs=0.15)
    assert COORDINATE_CONVENTION["y"] == "increases top to bottom"


def test_brightness_ordering_and_ids() -> None:
    amplitudes = [2000.0, 1000.0, 500.0, 250.0, 120.0]
    stars = [Star(40.0 + 40 * i, 100.0, a, 4.0) for i, a in enumerate(amplitudes)]
    result = _detect(render_field(SHAPE, stars=stars), DetectionConfig(fwhm=4.0))

    matched = [nearest(result.sources, s.x, s.y)[0] for s in stars]
    assert [m.source_id for m in matched] == sorted(m.source_id for m in matched)
    assert all(m.accepted for m in matched)
    fluxes = [m.flux for m in matched]
    assert fluxes == sorted(fluxes, reverse=True)
    assert result.sources[0].source_id == 1
    assert [s.source_id for s in result.sources] == list(range(1, len(result.sources) + 1))


def test_faint_star_snr_and_flux_are_sensible() -> None:
    star = Star(120.0, 100.0, 60.0, 4.0)  # peak 12 x noise
    result = _detect(render_field(SHAPE, stars=[star]), DetectionConfig(fwhm=4.0))
    source, distance = nearest(result.sources, star.x, star.y)
    assert distance < 0.5 and source.accepted
    # Aperture of 1 FWHM holds ~94% of a Gaussian's 2 pi sigma^2 A flux.
    total = 2 * np.pi * (4.0 / 2.3548) ** 2 * 60.0
    assert source.flux == pytest.approx(0.94 * total, rel=0.2)
    assert source.snr > 10


def test_pure_noise_yields_few_accepted_sources() -> None:
    result = _detect(render_field((256, 256), noise=5.0, seed=3), DetectionConfig(fwhm=4.0))
    assert len(result.accepted_sources) <= 2


def test_gradient_background_with_stars() -> None:
    stars = grid_stars(SHAPE, spacing=40, amplitude=500.0, fwhm=4.0)
    data = render_field(SHAPE, gradient=(0.5, 0.3), stars=stars)
    result = _detect(data)
    recovered = [nearest(result.accepted_sources, s.x, s.y)[1] < 0.3 for s in stars]
    assert all(recovered)


def test_near_edge_stars_are_flagged_not_lost() -> None:
    stars = [Star(7.0, 100.0, 1000.0, 4.0), Star(120.0, 5.5, 1000.0, 4.0)]
    result = _detect(render_field(SHAPE, stars=stars), DetectionConfig(fwhm=4.0))
    for star in stars:
        source, distance = nearest(result.sources, star.x, star.y)
        assert distance < 0.5
        assert source.edge and source.accepted


def test_star_cut_by_edge_is_rejected_with_reason() -> None:
    result = _detect(
        render_field(SHAPE, stars=[Star(1.0, 100.0, 1500.0, 4.0)]), DetectionConfig(fwhm=4.0)
    )
    source, distance = nearest(result.sources, 1.0, 100.0)
    assert distance < 2.0
    assert "too_close_to_edge" in source.rejection_reasons


def test_saturated_star_is_detected_flagged_and_kept() -> None:
    stars = [Star(120.3, 100.6, 4000.0, 5.0), *grid_stars(SHAPE, 60, 120.0, 5.0, margin=30)]
    data = np.clip(render_field(SHAPE, background=20.0, noise=2.0, stars=stars), 0, 255)
    data = np.round(data).astype(np.uint8)
    result = _detect(data, image_format=ImageFormat.PNG, nominal_max=255)

    assert result.saturation_level == 255.0 and result.saturation_source == "raster_nominal_max"
    source, distance = nearest(result.sources, 120.3, 100.6)
    assert distance < 0.5
    assert source.saturated and source.accepted
    assert source.centroid_method == "saturated_core"
    assert source.n_saturated_pixels > 10
    # Any other detection on the same saturated core is a rejected duplicate.
    duplicates = [s for s in result.sources if s.duplicate_of == source.source_id]
    assert all(not s.accepted for s in duplicates)


def test_elongated_star_is_detected() -> None:
    star = Star(120.0, 100.0, 900.0, fwhm=6.0, axis_ratio=0.6)
    result = _detect(render_field(SHAPE, stars=[star]), DetectionConfig(fwhm=4.5))
    _, distance = nearest(result.accepted_sources, star.x, star.y)
    assert distance < 0.3


def test_broad_bright_source_missed_by_daofind_is_recovered() -> None:
    # Much broader than the configured kernel: DAOFIND cannot fit it, the peak search can.
    star = Star(120.0, 100.0, 3000.0, fwhm=16.0)
    result = _detect(render_field(SHAPE, stars=[star]), DetectionConfig(fwhm=3.0))
    source, distance = nearest(result.sources, star.x, star.y)
    assert distance < 1.0
    assert source.centroid_method in ("daofind", "peak_com")


def test_xy_flux_for_plate_solving() -> None:
    stars = grid_stars(SHAPE, spacing=40, amplitude=800.0, fwhm=4.0)
    result = _detect(render_field(SHAPE, stars=stars))
    table = result.xy_flux()
    assert table.shape == (len(result.accepted_sources), 3)
    assert np.all(np.diff(table[:, 2]) <= 0)  # brightest first
    top = result.brightest(3)
    assert [s.flux for s in top] == list(table[:3, 2])


def test_preprocessing_result_not_modified() -> None:
    preprocessed = as_preprocessed(render_field(SHAPE, stars=grid_stars(SHAPE, 40, 800.0, 4.0)))
    data = preprocessed.image.data.copy()
    normalized = preprocessed.normalized.copy()
    detect_sources(preprocessed)
    np.testing.assert_array_equal(preprocessed.image.data, data)
    np.testing.assert_array_equal(preprocessed.normalized, normalized)


def test_detection_is_deterministic() -> None:
    data = render_field(SHAPE, stars=grid_stars(SHAPE, 40, 300.0, 4.0))
    first, second = _detect(data), _detect(data)
    # NaN-aware comparison (e.g. saturated_core_axis_ratio is NaN for unsaturated sources).
    np.testing.assert_equal(
        [s.to_dict() for s in first.sources], [s.to_dict() for s in second.sources]
    )


def test_empty_field_has_no_candidates_warning() -> None:
    result = _detect(render_field((64, 64), noise=5.0, seed=1), DetectionConfig(fwhm=4.0))
    if not result.sources:
        assert any("no source candidates" in w for w in result.warnings)
    assert result.xy_flux().shape[1] == 3

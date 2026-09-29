from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astroidentify import PreprocessingConfig, preprocess, preprocess_image, save_outputs
from astroidentify.exceptions import OutputError
from astroidentify.preprocessing.loader import image_from_array
from astroidentify.preprocessing.outputs import default_output_dir, to_jsonable
from astroidentify.preprocessing.preview import render_preview
from astroidentify.types import ImageFormat
from tests.conftest import star_field


def _load_json(path: Path) -> dict:
    def reject(constant: str) -> None:
        raise ValueError(f"non-standard JSON constant {constant}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def test_outputs_are_written_and_reloadable(simple_fits: Path, tmp_path: Path) -> None:
    result = preprocess_image(simple_fits)
    paths = save_outputs(result, tmp_path / "out")

    assert paths.processed.is_file() and paths.preview.is_file() and paths.metadata.is_file()
    assert paths.mask is None and not (tmp_path / "out" / "valid_mask.npy").exists()
    reloaded = np.load(paths.processed, allow_pickle=False)
    np.testing.assert_array_equal(reloaded, result.normalized)
    assert reloaded.dtype == np.float32


def test_metadata_json(simple_fits: Path, tmp_path: Path) -> None:
    result = preprocess_image(simple_fits)
    meta = _load_json(save_outputs(result, tmp_path / "out").metadata)

    assert meta["schema_version"] == 1
    assert meta["source"] == "field.fits"
    assert meta["format"] == "FITS"
    assert (meta["width"], meta["height"], meta["channels"]) == (160, 128, 1)
    assert meta["background_level"] == pytest.approx(result.background_level)
    assert meta["noise_sigma"] == pytest.approx(result.noise_sigma)
    assert meta["normalization"]["method"] == "percentile"
    assert meta["normalization"]["lower_percentile"] == 1.0
    assert meta["normalization"]["upper_percentile"] == 99.5
    assert meta["source_metadata"]["fits"]["summary"]["OBJECT"] == "SYNTHETIC"
    assert meta["artifacts"] == {
        "processed": "processed.npy",
        "preview": "preview.png",
        "metadata": "metadata.json",
        "valid_mask": None,
    }
    assert len(meta["source_sha256"]) == 64
    assert meta["config"] == PreprocessingConfig().to_dict()


def test_rgb_metadata_has_per_channel_background(rgb_jpeg: Path, tmp_path: Path) -> None:
    meta = _load_json(save_outputs(preprocess_image(rgb_jpeg), tmp_path / "o").metadata)
    assert meta["channels"] == 3
    assert meta["background"]["plane"] == "channel_mean"
    assert len(meta["background"]["per_channel"]) == 3


def test_mask_written_only_when_needed(fits_writer: Callable[..., Path], tmp_path: Path) -> None:
    data = star_field().astype(np.float32)
    data[0, :] = np.nan
    result = preprocess_image(fits_writer("holes.fits", fits.PrimaryHDU(data)))
    out = tmp_path / "out"
    paths = save_outputs(result, out)

    assert paths.mask is not None
    mask = np.load(paths.mask)
    assert mask.dtype == bool and not mask[0].any() and mask[1:].all()
    assert _load_json(paths.metadata)["artifacts"]["valid_mask"] == "valid_mask.npy"

    # A later run without invalid pixels into the same directory removes the stale mask.
    clean = preprocess(image_from_array(star_field(), image_format=ImageFormat.FITS))
    assert save_outputs(clean, out).mask is None
    assert not (out / "valid_mask.npy").exists()


def test_metadata_is_strict_json_even_with_non_finite_values() -> None:
    converted = to_jsonable({"a": float("nan"), "b": np.float32(1.5), "c": (1, np.int64(2))})
    assert converted == {"a": None, "b": 1.5, "c": [1, 2]}
    json.dumps(converted, allow_nan=False)


def test_output_path_is_a_file(simple_fits: Path, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(OutputError, match="not a directory"):
        save_outputs(preprocess_image(simple_fits), blocker)


def test_refuses_to_overwrite_source(tmp_path: Path) -> None:
    source = tmp_path / "preview.png"
    Image.fromarray(np.full((16, 16), 50, dtype=np.uint8)).save(source)
    before = source.read_bytes()
    with pytest.raises(OutputError, match="overwrite the source"):
        save_outputs(preprocess_image(source), tmp_path)
    assert source.read_bytes() == before


def test_default_output_dir() -> None:
    assert default_output_dir("data/raw/m57.jpg") == Path("outputs/m57")
    assert default_output_dir("m31.fits.gz") == Path("outputs/m31")


# --------------------------------------------------------------------------- preview


def test_preview_grayscale_and_color(gray_png: Path, rgb_jpeg: Path) -> None:
    gray = render_preview(preprocess_image(gray_png).image)
    assert gray.mode == "L" and gray.size == (160, 128)
    color = render_preview(preprocess_image(rgb_jpeg).image)
    assert color.mode == "RGB" and color.size == (160, 128)


def test_preview_file(rgb_png: Path, tmp_path: Path) -> None:
    paths = save_outputs(preprocess_image(rgb_png), tmp_path / "out")
    with Image.open(paths.preview) as preview:
        assert preview.format == "PNG"
        assert preview.size == (160, 128)


def test_preview_downscale_preserves_aspect_ratio(simple_fits: Path) -> None:
    config = PreprocessingConfig(preview_max_dimension=80)
    preview = render_preview(preprocess_image(simple_fits, config).image, config)
    assert preview.size == (80, 64)


def test_fits_preview_is_flipped_for_display() -> None:
    data = np.zeros((32, 32))
    data[2, 5] = 1000.0  # row 2 of FITS data is near the *bottom* when displayed
    config = PreprocessingConfig(preview_stretch="linear")
    fits_preview = np.asarray(
        render_preview(image_from_array(data, image_format=ImageFormat.FITS), config)
    )
    png_preview = np.asarray(
        render_preview(image_from_array(data, image_format=ImageFormat.PNG), config)
    )
    assert fits_preview[29, 5] == 255 and fits_preview[2, 5] == 0
    assert png_preview[2, 5] == 255


def test_preview_does_not_change_scientific_arrays(simple_fits: Path) -> None:
    result = preprocess_image(simple_fits)
    normalized = result.normalized.copy()
    data = result.image.data.copy()
    render_preview(result.image, PreprocessingConfig(preview_stretch="asinh"))
    np.testing.assert_array_equal(result.normalized, normalized)
    np.testing.assert_array_equal(result.image.data, data)


def test_preview_handles_nan() -> None:
    data = star_field()
    data[:10] = np.nan
    preview = np.asarray(render_preview(image_from_array(data, image_format=ImageFormat.FITS)))
    assert preview.dtype == np.uint8
    assert np.all(preview[-10:] == 0)  # flipped: NaN rows are at the bottom, drawn black

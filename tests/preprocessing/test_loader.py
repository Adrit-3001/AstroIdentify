from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astroidentify.config import PreprocessingConfig
from astroidentify.exceptions import (
    CorruptImageError,
    ImageLoadError,
    ImageNotFoundError,
    InvalidImageError,
    MalformedFitsError,
    NoImageDataError,
    UnsupportedDimensionsError,
    UnsupportedFormatError,
)
from astroidentify.preprocessing.loader import image_from_array, load_image
from astroidentify.types import WORKING_DTYPE, ImageFormat
from tests.conftest import star_field, to_uint8

# --------------------------------------------------------------------------- raster


def test_grayscale_png(gray_png: Path) -> None:
    image = load_image(gray_png)
    expected = np.asarray(Image.open(gray_png))

    assert image.format is ImageFormat.PNG
    assert image.data.shape == expected.shape == (128, 160)
    assert image.data.dtype == WORKING_DTYPE
    assert not image.is_color and image.channels == 1
    assert (image.width, image.height) == (160, 128)
    np.testing.assert_array_equal(image.data, expected)
    assert image.display_origin == "upper"
    assert image.original_dtype == "uint8"
    assert image.metadata["raster"]["nominal_max"] == 255
    assert image.source_path == gray_png.resolve()


def test_rgb_png(rgb_png: Path) -> None:
    image = load_image(rgb_png)
    assert image.is_color and image.channels == 3
    assert image.data.shape == (128, 160, 3)
    np.testing.assert_array_equal(image.data, np.asarray(Image.open(rgb_png)))


def test_rgb_jpeg(rgb_jpeg: Path) -> None:
    image = load_image(rgb_jpeg)
    assert image.format is ImageFormat.JPEG
    assert image.data.shape == (128, 160, 3)
    assert image.data.dtype == WORKING_DTYPE


def test_grayscale_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "gray.jpeg"
    Image.fromarray(to_uint8(star_field())).save(path)
    image = load_image(path)
    assert image.format is ImageFormat.JPEG
    assert image.data.shape == (128, 160)


def test_16bit_grayscale_png_keeps_full_range(tmp_path: Path) -> None:
    data = np.clip(star_field() * 20, 0, 65535).astype(np.uint16)
    path = tmp_path / "deep.png"
    Image.fromarray(data).save(path)

    image = load_image(path)
    np.testing.assert_array_equal(image.data, data.astype(np.float32))
    assert image.metadata["raster"]["png_bit_depth"] == 16
    assert image.metadata["raster"]["nominal_max"] == 65535


def test_rgba_png_drops_alpha(tmp_path: Path) -> None:
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 3] = 200, 128
    path = tmp_path / "rgba.png"
    Image.fromarray(rgba).save(path)

    image = load_image(path)
    assert image.data.shape == (16, 16, 3)
    assert image.original_shape == (16, 16, 4)
    assert image.metadata["raster"]["alpha_dropped"] is True
    assert np.all(image.data[..., 0] == 200)


def test_palette_png_is_converted_to_rgb(tmp_path: Path) -> None:
    path = tmp_path / "palette.png"
    Image.fromarray(to_uint8(star_field())).convert("P").save(path)
    image = load_image(path)
    assert image.data.shape == (128, 160, 3)
    assert image.metadata["raster"]["pil_mode"] == "P"


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    array = np.zeros((10, 20), dtype=np.uint8)
    array[0, 0] = 255
    img = Image.fromarray(array)
    exif = img.getexif()
    exif[0x0112] = 6  # stored image must be rotated 90 degrees clockwise for display
    path = tmp_path / "rotated.jpg"
    img.save(path, exif=exif, quality=100)

    image = load_image(path)
    assert image.data.shape == (20, 10)
    assert image.original_shape == (10, 20)
    assert image.metadata["raster"]["orientation_applied"] is True


def test_exif_capture_metadata_is_preserved(tmp_path: Path) -> None:
    img = Image.fromarray(to_uint8(star_field()))
    exif = img.getexif()
    exif[0x010F] = "TestCam"  # Make
    exif.get_ifd(0x8769)[0x9003] = "2026:09:01 22:15:00"  # Exif IFD: DateTimeOriginal
    path = tmp_path / "exif.jpg"
    img.save(path, exif=exif)

    exif_meta = load_image(path).metadata["exif"]
    assert exif_meta["Make"] == "TestCam"
    assert exif_meta["DateTimeOriginal"] == "2026:09:01 22:15:00"


def test_content_wins_over_misleading_extension(tmp_path: Path) -> None:
    path = tmp_path / "actually_png.jpg"
    Image.fromarray(to_uint8(star_field())).save(path, format="PNG")
    image = load_image(path)
    assert image.format is ImageFormat.PNG
    assert any("content is PNG" in w for w in image.warnings)


def test_data_is_read_only(gray_png: Path) -> None:
    image = load_image(gray_png)
    with pytest.raises(ValueError):
        image.data[0, 0] = 1.0


# --------------------------------------------------------------------------- errors


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ImageNotFoundError, match="not found"):
        load_image(tmp_path / "missing.png")


def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ImageNotFoundError, match="not a file"):
        load_image(tmp_path)


@pytest.mark.parametrize("name", ["image.gif", "image.tiff", "notes.txt", "noextension"])
def test_unsupported_extension(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"data")
    with pytest.raises(UnsupportedFormatError, match="unsupported file type"):
        load_image(path)


def test_other_image_format_with_supported_extension(tmp_path: Path) -> None:
    path = tmp_path / "really_a_gif.png"
    Image.fromarray(np.zeros((16, 16), dtype=np.uint8)).save(path, format="GIF")
    with pytest.raises(UnsupportedFormatError, match="GIF"):
        load_image(path)


@pytest.mark.parametrize("name", ["garbage.png", "garbage.jpg"])
def test_garbage_raster(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"this is not an image" * 10)
    with pytest.raises(CorruptImageError):
        load_image(path)


def test_truncated_jpeg(rgb_jpeg: Path) -> None:
    content = rgb_jpeg.read_bytes()
    rgb_jpeg.write_bytes(content[: len(content) // 2])
    with pytest.raises(CorruptImageError):
        load_image(rgb_jpeg)


def test_truncated_png(gray_png: Path) -> None:
    content = gray_png.read_bytes()
    gray_png.write_bytes(content[: len(content) // 2])
    with pytest.raises(CorruptImageError):
        load_image(gray_png)


def test_png_with_invalid_exif_crc_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "invalid-exif.png"
    image = Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8))
    image.save(path)
    png = path.read_bytes()
    chunk_type = b"eXIf"
    chunk_data = b"invalid exif"
    invalid_crc = (zlib.crc32(chunk_type + chunk_data) ^ 1) & 0xFFFFFFFF
    chunk = (
        struct.pack(">I", len(chunk_data))
        + chunk_type
        + chunk_data
        + struct.pack(">I", invalid_crc)
    )
    path.write_bytes(png[:33] + chunk + png[33:])

    loaded = load_image(path)

    assert loaded.data.shape == (16, 16, 3)
    assert any("EXIF metadata CRC" in warning for warning in loaded.warnings)


def test_image_too_small(tmp_path: Path) -> None:
    path = tmp_path / "tiny.png"
    Image.fromarray(np.zeros((4, 100), dtype=np.uint8)).save(path)
    with pytest.raises(InvalidImageError, match="at least 8"):
        load_image(path)
    assert load_image(path, PreprocessingConfig(min_dimension=4)).data.shape == (4, 100)


def test_all_errors_share_a_base_class(tmp_path: Path) -> None:
    with pytest.raises(ImageLoadError):
        load_image(tmp_path / "missing.fits")


# --------------------------------------------------------------------------- FITS


def test_fits_loading_preserves_values_and_header(simple_fits: Path) -> None:
    image = load_image(simple_fits)
    expected = star_field().astype(np.float32)

    assert image.format is ImageFormat.FITS
    assert image.display_origin == "lower"
    np.testing.assert_array_equal(image.data, expected)
    assert image.header is not None and image.header["OBJECT"] == "SYNTHETIC"

    fits_meta = image.metadata["fits"]
    assert fits_meta["hdu_index"] == 0
    assert fits_meta["summary"] == {"OBJECT": "SYNTHETIC", "EXPTIME": 30.0}
    assert fits_meta["has_wcs_keywords"] is True
    cards = fits_meta["header"]
    assert ["EXPTIME", 30.0, "exposure time [s]"] in cards
    assert [c[1] for c in cards if c[0] == "HISTORY"] == [
        "first history card",
        "second history card",
    ]


def test_fits_uses_first_extension_when_primary_is_empty(
    fits_writer: Callable[..., Path],
) -> None:
    data = star_field().astype(np.float32)
    path = fits_writer("ext.fits", fits.PrimaryHDU(), fits.ImageHDU(data, name="SCI"))
    image = load_image(path)
    np.testing.assert_array_equal(image.data, data)
    assert image.metadata["fits"]["hdu_index"] == 1
    assert image.metadata["fits"]["hdu_name"] == "SCI"
    assert image.metadata["fits"]["primary_header"] is not None


def test_fits_multiple_images_selects_first_deterministically(
    fits_writer: Callable[..., Path],
) -> None:
    first = np.full((16, 16), 1.0, dtype=np.float32)
    second = np.full((16, 16), 2.0, dtype=np.float32)
    path = fits_writer("multi.fits", fits.PrimaryHDU(first), fits.ImageHDU(second))

    image = load_image(path)
    assert image.metadata["fits"]["usable_hdus"] == [0, 1]
    assert np.all(image.data == 1.0)

    requested = load_image(path, PreprocessingConfig(fits_hdu=1))
    assert np.all(requested.data == 2.0)
    assert requested.metadata["fits"]["hdu_selection"] == "requested"


def test_fits_singleton_axes_are_squeezed(fits_writer: Callable[..., Path]) -> None:
    data = star_field().astype(np.float32)
    path = fits_writer("cube1.fits", fits.PrimaryHDU(data[np.newaxis]))
    image = load_image(path)
    assert image.data.shape == data.shape
    assert image.original_shape == (1, *data.shape)


def test_fits_cube_is_rejected(fits_writer: Callable[..., Path]) -> None:
    path = fits_writer("cube.fits", fits.PrimaryHDU(np.zeros((3, 16, 16), dtype=np.float32)))
    with pytest.raises(UnsupportedDimensionsError, match=r"\(3, 16, 16\)"):
        load_image(path)


def test_fits_one_dimensional_is_rejected(fits_writer: Callable[..., Path]) -> None:
    path = fits_writer("spectrum.fits", fits.PrimaryHDU(np.zeros(100, dtype=np.float32)))
    with pytest.raises(UnsupportedDimensionsError):
        load_image(path)


def test_fits_table_only(fits_writer: Callable[..., Path]) -> None:
    table = fits.BinTableHDU.from_columns([fits.Column(name="x", format="E", array=[1.0, 2.0])])
    path = fits_writer("table.fits", fits.PrimaryHDU(), table)
    with pytest.raises(NoImageDataError):
        load_image(path)


def test_fits_requested_hdu_out_of_range(simple_fits: Path) -> None:
    with pytest.raises(NoImageDataError, match="only 1 HDU"):
        load_image(simple_fits, PreprocessingConfig(fits_hdu=3))


def test_fits_scaled_integers_and_blank(fits_writer: Callable[..., Path]) -> None:
    raw = np.arange(256, dtype=np.int16).reshape(16, 16)
    raw[0, 0] = -32768
    hdu = fits.PrimaryHDU(raw)
    hdu.header["BLANK"] = -32768
    hdu.header["BSCALE"] = 2.0
    hdu.header["BZERO"] = 10.0
    path = fits_writer("scaled.fits", hdu)

    image = load_image(path)
    assert np.isnan(image.data[0, 0])
    assert image.data[0, 1] == pytest.approx(1 * 2.0 + 10.0)
    assert image.data[15, 15] == pytest.approx(255 * 2.0 + 10.0)


def test_fits_nan_and_inf_become_nan(fits_writer: Callable[..., Path]) -> None:
    data = star_field().astype(np.float32)
    data[0, 0], data[1, 1], data[2, 2] = np.nan, np.inf, -np.inf
    image = load_image(fits_writer("bad_pixels.fits", fits.PrimaryHDU(data)))

    assert np.isnan(image.data[[0, 1, 2], [0, 1, 2]]).all()
    assert np.isfinite(image.data).sum() == data.size - 3
    assert any("infinite" in w for w in image.warnings)


def test_fits_all_nan_is_rejected(fits_writer: Callable[..., Path]) -> None:
    path = fits_writer("nan.fits", fits.PrimaryHDU(np.full((16, 16), np.nan, np.float32)))
    with pytest.raises(InvalidImageError, match="no finite"):
        load_image(path)


def test_malformed_fits(tmp_path: Path) -> None:
    path = tmp_path / "bad.fits"
    path.write_bytes(b"SIMPLE? not really" * 200)
    with pytest.raises(MalformedFitsError):
        load_image(path)


def test_truncated_fits(simple_fits: Path) -> None:
    content = simple_fits.read_bytes()
    simple_fits.write_bytes(content[: 2880 + 1000])  # full header block, partial data
    with pytest.raises(MalformedFitsError):
        load_image(simple_fits)


def test_gzipped_fits(tmp_path: Path) -> None:
    path = tmp_path / "field.fits.gz"
    fits.PrimaryHDU(star_field().astype(np.float32)).writeto(path)
    assert load_image(path).data.shape == (128, 160)


# --------------------------------------------------------------------------- arrays


def test_image_from_array_does_not_modify_input() -> None:
    data = star_field()
    data[0, 0] = np.inf
    original = data.copy()
    image = image_from_array(data, image_format=ImageFormat.FITS)
    np.testing.assert_array_equal(data, original)
    assert np.isnan(image.data[0, 0])


@pytest.mark.parametrize("shape", [(16,), (16, 16, 4), (16, 16, 2), (2, 16, 16, 3)])
def test_image_from_array_rejects_bad_shapes(shape: tuple[int, ...]) -> None:
    with pytest.raises(InvalidImageError):
        image_from_array(np.zeros(shape), image_format=ImageFormat.PNG)

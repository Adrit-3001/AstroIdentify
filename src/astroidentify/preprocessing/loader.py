"""Load JPEG, PNG and FITS files into a format-independent :class:`AstronomyImage`.

All format-specific decoding lives here. Every loader funnels its array through
:func:`image_from_array`, the single place where dtype conversion, NaN/Inf policy and
shape validation happen, so downstream code never needs to know the source format.
"""

from __future__ import annotations

import io
import logging
import struct
import warnings
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from astroidentify.config import PreprocessingConfig
from astroidentify.exceptions import (
    AstroIdentifyError,
    CorruptImageError,
    ImageNotFoundError,
    InvalidImageError,
    MalformedFitsError,
    NoImageDataError,
    UnsupportedDimensionsError,
    UnsupportedFormatError,
)
from astroidentify.types import WORKING_DTYPE, AstronomyImage, DisplayOrigin, ImageFormat

logger = logging.getLogger(__name__)

_RASTER_EXTENSIONS: dict[str, ImageFormat] = {
    ".jpg": ImageFormat.JPEG,
    ".jpeg": ImageFormat.JPEG,
    ".png": ImageFormat.PNG,
}
_FITS_EXTENSIONS = (".fits", ".fit", ".fts")
SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    *_RASTER_EXTENSIONS,
    *_FITS_EXTENSIONS,
    *(ext + ".gz" for ext in _FITS_EXTENSIONS),
)

# Pillow decoders allowed to open raster files. Pillow's JPEG opener also returns
# multi-picture JPEGs (some cameras), reported as "MPO"; frame 0 is a normal JPEG.
_PILLOW_OPEN_FORMATS = ("JPEG", "PNG")
_PILLOW_FORMATS: dict[str, ImageFormat] = {
    "JPEG": ImageFormat.JPEG,
    "MPO": ImageFormat.JPEG,
    "PNG": ImageFormat.PNG,
}

# Pillow modes that decode directly to a usable array, and conversions for the others.
_NATIVE_MODES = {"L", "RGB", "I", "F", "I;16", "I;16L", "I;16B", "I;16N"}
_MODE_CONVERSIONS = {
    "1": "L",
    "LA": "L",
    "La": "L",
    "P": "RGB",
    "PA": "RGB",
    "RGBA": "RGB",
    "RGBa": "RGB",
    "RGBX": "RGB",
    "CMYK": "RGB",
    "YCbCr": "RGB",
    "LAB": "RGB",
    "HSV": "RGB",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_COLOR_TYPES_WITH_RGB = {2, 6}

# FITS keywords worth surfacing in a compact summary (the full header is kept too).
_FITS_SUMMARY_KEYWORDS = (
    "OBJECT",
    "DATE-OBS",
    "MJD-OBS",
    "EXPTIME",
    "EXPOSURE",
    "TELESCOP",
    "INSTRUME",
    "FILTER",
    "BUNIT",
    "GAIN",
    "SATURATE",
    "RA",
    "DEC",
    "OBJCTRA",
    "OBJCTDEC",
    "EQUINOX",
    "RADESYS",
    "XBINNING",
    "YBINNING",
    "IMAGETYP",
)
_FITS_IMAGE_HDU_TYPES = (fits.PrimaryHDU, fits.ImageHDU, fits.CompImageHDU)

_EXIF_SKIP_TAGS = {"MakerNote", "ExifOffset", "GPSInfo", "PrintImageMatching", "UserComment"}
_MAX_METADATA_STRING = 512


def detect_format(path: Path) -> ImageFormat:
    """Return the format implied by ``path``'s extension.

    Raises:
        UnsupportedFormatError: If the extension is not a supported image type.
    """
    name = path.name.lower()
    for ext in _FITS_EXTENSIONS:
        if name.endswith(ext) or name.endswith(ext + ".gz"):
            return ImageFormat.FITS
    fmt = _RASTER_EXTENSIONS.get(path.suffix.lower())
    if fmt is None:
        raise UnsupportedFormatError(
            f"unsupported file type {path.suffix or '(no extension)'!r} for {path}; "
            f"supported extensions: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    return fmt


def load_image(path: str | Path, config: PreprocessingConfig | None = None) -> AstronomyImage:
    """Load a JPEG, PNG or FITS file.

    Args:
        path: Path to the image file.
        config: Preprocessing configuration (uses ``fits_hdu`` and ``min_dimension``).

    Returns:
        The loaded image, with a read-only ``float32`` data array.

    Raises:
        ImageNotFoundError: The path does not exist or is not a file.
        UnsupportedFormatError: The file type is not supported.
        CorruptImageError: The file cannot be decoded (``MalformedFitsError`` for FITS).
        NoImageDataError / UnsupportedDimensionsError: No usable 2-D FITS image data.
        InvalidImageError: The decoded image is too small or has no finite pixels.
    """
    config = config or PreprocessingConfig()
    path = Path(path).expanduser()
    if not path.exists():
        raise ImageNotFoundError(f"input file not found: {path}")
    if not path.is_file():
        raise ImageNotFoundError(f"input path is not a file: {path}")
    path = path.resolve()

    fmt = detect_format(path)
    logger.info("Loading %s as %s", path, fmt.value)
    if fmt is ImageFormat.FITS:
        return _load_fits(path, config)
    return _load_raster(path, fmt, config)


def image_from_array(
    data: np.ndarray,
    *,
    image_format: ImageFormat,
    source_path: str | Path = "<memory>",
    display_origin: DisplayOrigin | None = None,
    original_shape: tuple[int, ...] | None = None,
    original_dtype: str | None = None,
    metadata: dict[str, Any] | None = None,
    header: fits.Header | None = None,
    load_warnings: tuple[str, ...] | list[str] = (),
    config: PreprocessingConfig | None = None,
) -> AstronomyImage:
    """Validate an array and wrap it as an :class:`AstronomyImage`.

    The input array is never modified: it is copied to ``WORKING_DTYPE``. ``+/-Inf`` is
    converted to NaN so that NaN is the single "invalid pixel" marker downstream.

    Raises:
        InvalidImageError: Unsupported shape, too small, or no finite pixels.
    """
    config = config or PreprocessingConfig()
    source = np.asarray(data)
    if not (np.issubdtype(source.dtype, np.number) or source.dtype == np.bool_):
        raise InvalidImageError(f"image data has non-numeric dtype {source.dtype}")
    if source.ndim == 3 and source.shape[2] == 1:
        source = source[:, :, 0]
    if not (source.ndim == 2 or (source.ndim == 3 and source.shape[2] == 3)):
        raise InvalidImageError(
            f"image data must have shape (H, W) or (H, W, 3); got {source.shape}"
        )
    height, width = source.shape[:2]
    if min(height, width) < config.min_dimension:
        raise InvalidImageError(
            f"image is {width} x {height} pixels; both dimensions must be at least "
            f"{config.min_dimension}"
        )

    array = np.array(source, dtype=WORKING_DTYPE, copy=True)
    all_warnings = list(load_warnings)
    n_inf = int(np.count_nonzero(np.isinf(array)))
    if n_inf:
        array[np.isinf(array)] = np.nan
        _warn(all_warnings, f"{n_inf} infinite pixel value(s) treated as invalid (NaN)")
    n_finite = int(np.count_nonzero(np.isfinite(array)))
    if n_finite == 0:
        raise InvalidImageError("image contains no finite pixel values")
    n_invalid = array.size - n_finite
    if n_invalid:
        _warn(
            all_warnings,
            f"{n_invalid} of {array.size} pixel value(s) are invalid (NaN/Inf/BLANK) and "
            "are excluded from statistics",
        )
    array.flags.writeable = False

    if display_origin is None:
        display_origin = "lower" if image_format is ImageFormat.FITS else "upper"
    return AstronomyImage(
        data=array,
        source_path=Path(source_path),
        format=image_format,
        original_shape=tuple(original_shape or source.shape),
        original_dtype=original_dtype or source.dtype.name,
        display_origin=display_origin,
        metadata=dict(metadata or {}),
        header=header,
        warnings=tuple(all_warnings),
    )


# --------------------------------------------------------------------------- raster


def _load_raster(path: Path, expected: ImageFormat, config: PreprocessingConfig) -> AstronomyImage:
    load_warnings: list[str] = []
    raster_source: Path | io.BytesIO = path
    if expected is ImageFormat.PNG:
        repaired_png = _repair_invalid_exif_crc(path)
        if repaired_png is not None:
            raster_source = io.BytesIO(repaired_png)
            _warn(
                load_warnings,
                "invalid PNG EXIF metadata CRC was ignored; pixel data was preserved",
            )
    try:
        with Image.open(raster_source, formats=_PILLOW_OPEN_FORMATS) as img:
            detected = _PILLOW_FORMATS[img.format or ""]
            if detected is not expected:
                _warn(
                    load_warnings,
                    f"file extension suggests {expected.value} but content is "
                    f"{detected.value}; using {detected.value}",
                )
            n_frames = int(getattr(img, "n_frames", 1))
            if n_frames > 1:
                _warn(load_warnings, f"file contains {n_frames} frames; using the first")
            img.load()
            exif = img.getexif()
            orientation = int(exif.get(ExifTags.Base.Orientation, 1))
            metadata = _raster_metadata(path, img, exif, detected)
            original_shape = _pillow_shape(img)
            if orientation != 1:
                # Losslessly rotate/flip so array rows match what image viewers display.
                img = ImageOps.exif_transpose(img)
            array, raster_meta = _pillow_to_array(img)
    except UnidentifiedImageError as exc:
        raise _unidentified_error(path, expected) from exc
    except Image.DecompressionBombError as exc:
        raise InvalidImageError(f"{path} is too large to decode safely: {exc}") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptImageError(f"could not decode {expected.value} file {path}: {exc}") from exc

    metadata["raster"].update(raster_meta)
    metadata["raster"]["exif_orientation"] = orientation
    metadata["raster"]["orientation_applied"] = orientation != 1
    if raster_meta["alpha_dropped"]:
        logger.info("Dropped alpha channel from %s", path.name)

    png_depth = metadata["raster"].get("png_bit_depth")
    png_color = metadata["raster"].get("png_color_type")
    if png_depth == 16 and png_color in _PNG_COLOR_TYPES_WITH_RGB and array.dtype == np.uint8:
        _warn(
            load_warnings,
            "16-bit colour PNG was reduced to 8 bits per channel by the decoder (Pillow); "
            "convert to FITS or 16-bit grayscale PNG to keep full precision",
        )
    metadata["raster"]["nominal_max"] = _nominal_max(array.dtype, png_depth)

    return image_from_array(
        array,
        image_format=detected,
        source_path=path,
        original_shape=original_shape,
        original_dtype=array.dtype.name,
        metadata=metadata,
        load_warnings=load_warnings,
        config=config,
    )


def _unidentified_error(path: Path, expected: ImageFormat) -> Exception:
    """Distinguish "some other image format" from "not a decodable image at all"."""
    try:
        with Image.open(path) as img:
            actual = img.format
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        actual = None
    if actual:
        return UnsupportedFormatError(
            f"{path} contains {actual} image data, which is not supported "
            f"(expected {expected.value})"
        )
    return CorruptImageError(f"{path} is not a readable {expected.value} image")


def _repair_invalid_exif_crc(path: Path) -> bytes | None:
    """Return a PNG without a corrupt eXIf chunk, or ``None`` when no repair is needed."""
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if not content.startswith(_PNG_SIGNATURE):
        return None

    position = len(_PNG_SIGNATURE)
    repaired = bytearray(content[:position])
    removed = False
    while position + 12 <= len(content):
        length = struct.unpack(">I", content[position : position + 4])[0]
        end = position + 12 + length
        if end > len(content):
            return None
        chunk = content[position:end]
        chunk_type = chunk[4:8]
        chunk_data = chunk[8 : 8 + length]
        stored_crc = struct.unpack(">I", chunk[8 + length : end])[0]
        calculated_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if chunk_type == b"eXIf" and stored_crc != calculated_crc:
            removed = True
        else:
            repaired.extend(chunk)
        position = end
        if chunk_type == b"IEND":
            break

    return bytes(repaired) if removed else None


def _pillow_shape(img: Image.Image) -> tuple[int, ...]:
    bands = len(img.getbands())
    return (img.height, img.width) if bands == 1 else (img.height, img.width, bands)


def _pillow_to_array(img: Image.Image) -> tuple[np.ndarray, dict[str, Any]]:
    mode = img.mode
    alpha_dropped = "A" in mode or mode in {"RGBa", "La"}
    if mode in _NATIVE_MODES:
        converted = img
    elif mode in _MODE_CONVERSIONS:
        converted = img.convert(_MODE_CONVERSIONS[mode])
    else:
        raise CorruptImageError(f"unsupported pixel mode {mode!r}")
    array = np.asarray(converted)
    info = {
        "pil_mode": mode,
        "converted_mode": converted.mode if converted is not img else None,
        "alpha_dropped": alpha_dropped,
    }
    return array, info


def _nominal_max(dtype: np.dtype, png_bit_depth: int | None) -> int | None:
    if dtype == np.uint8:
        return 255
    if dtype == np.uint16 or png_bit_depth == 16:
        return 65535
    return None


def _raster_metadata(
    path: Path, img: Image.Image, exif: Image.Exif, fmt: ImageFormat
) -> dict[str, Any]:
    raster: dict[str, Any] = {
        "pillow_format": img.format,
        "n_frames": int(getattr(img, "n_frames", 1)),
        "icc_profile_present": bool(img.info.get("icc_profile")),
        "info": _simple_info(img.info),
    }
    if fmt is ImageFormat.PNG:
        depth, color_type = _read_png_ihdr(path)
        raster["png_bit_depth"] = depth
        raster["png_color_type"] = color_type
    metadata: dict[str, Any] = {"raster": raster}
    exif_data = _exif_to_dict(exif)
    if exif_data:
        metadata["exif"] = exif_data
    return metadata


def _read_png_ihdr(path: Path) -> tuple[int | None, int | None]:
    """Read bit depth and colour type from the PNG header (Pillow hides 16-bit RGB)."""
    with path.open("rb") as fh:
        head = fh.read(26)
    if len(head) < 26 or not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None, None
    return head[24], head[25]


def _simple_info(info: dict[str, Any]) -> dict[str, Any]:
    """Keep small scalar/text entries of Pillow's ``info`` dict (PNG text chunks, dpi...)."""
    out: dict[str, Any] = {}
    for key, value in info.items():
        simple = _simple_value(value)
        if simple is not None:
            out[str(key)] = simple
    return out


def _exif_to_dict(exif: Image.Exif) -> dict[str, Any]:
    """Extract human-readable EXIF tags, including capture time and GPS if present.

    Capture time and observer location are relevant to later milestones (e.g. solar
    system objects), so they are preserved rather than discarded.
    """
    try:
        # Base IFD (camera, orientation) plus the Exif sub-IFD (capture time, exposure).
        tags = [*exif.items(), *exif.get_ifd(ExifTags.IFD.Exif).items()]
        out = {
            name: simple
            for tag, value in tags
            if (name := ExifTags.TAGS.get(tag))
            and name not in _EXIF_SKIP_TAGS
            and (simple := _simple_value(value)) is not None
        }
        gps = {
            ExifTags.GPSTAGS.get(tag, str(tag)): simple
            for tag, value in exif.get_ifd(ExifTags.IFD.GPSInfo).items()
            if (simple := _simple_value(value)) is not None
        }
    except (KeyError, ValueError, TypeError, OSError) as exc:
        logger.debug("Could not read EXIF data: %s", exc)
        return {}
    if gps:
        out["GPS"] = gps
    return out


def _simple_value(value: Any) -> Any:
    """Convert EXIF/info values to JSON-friendly scalars; return None to drop the value."""
    if isinstance(value, bool | int | str):
        if isinstance(value, str):
            value = value.strip("\x00 ").strip()
            return value[:_MAX_METADATA_STRING] if value else None
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if hasattr(value, "numerator") and hasattr(value, "denominator"):  # IFDRational
        return float(value) if value.denominator else None
    if isinstance(value, tuple | list) and 0 < len(value) <= 16:
        items = [_simple_value(v) for v in value]
        return None if any(v is None for v in items) else items
    return None


# --------------------------------------------------------------------------- FITS


def _load_fits(path: Path, config: PreprocessingConfig) -> AstronomyImage:
    load_warnings: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            hdul = fits.open(path, memmap=False)
        except Exception as exc:  # astropy raises many exception types for bad files
            raise MalformedFitsError(f"{path} is not a readable FITS file: {exc}") from exc
        try:
            with hdul:
                hdus = [_describe_hdu(index, hdu) for index, hdu in enumerate(hdul)]
                index = _select_hdu(hdus, config.fits_hdu, path)
                hdu = hdul[index]
                raw = hdu.data
                if raw is None:
                    raise NoImageDataError(f"HDU {index} of {path} has no data")
                # Copy before the file closes; the working-dtype conversion copies again but
                # this keeps the array valid regardless of memory mapping.
                raw = np.array(raw)
                header = hdu.header.copy()
                primary_header = hdul[0].header.copy() if index != 0 else None
        except AstroIdentifyError:
            raise
        except Exception as exc:
            raise MalformedFitsError(f"could not read FITS data from {path}: {exc}") from exc

    for item in caught:
        _warn(load_warnings, f"FITS reader: {item.message}")

    selected = hdus[index]
    candidates = [h["index"] for h in hdus if h["usable"]]
    if config.fits_hdu is None and len(candidates) > 1:
        logger.info(
            "FITS file has usable image HDUs %s; using the first (HDU %d)", candidates, index
        )

    data = np.squeeze(raw)
    metadata = {
        "fits": {
            "hdu_index": index,
            "hdu_name": selected["name"],
            "hdu_selection": "requested" if config.fits_hdu is not None else "first_usable_2d",
            "usable_hdus": candidates,
            "hdus": hdus,
            "bitpix": header.get("BITPIX"),
            "bscale": header.get("BSCALE"),
            "bzero": header.get("BZERO"),
            "squeezed_singleton_axes": raw.ndim != data.ndim,
            "has_wcs_keywords": "CTYPE1" in header and "CTYPE2" in header,
            "summary": {
                key: _card_value(header[key]) for key in _FITS_SUMMARY_KEYWORDS if key in header
            },
            "header": _header_cards(header),
            "primary_header": _header_cards(primary_header) if primary_header else None,
        }
    }
    return image_from_array(
        data,
        image_format=ImageFormat.FITS,
        source_path=path,
        display_origin="lower",
        original_shape=tuple(raw.shape),
        original_dtype=raw.dtype.name,
        metadata=metadata,
        header=header,
        load_warnings=load_warnings,
        config=config,
    )


def _describe_hdu(index: int, hdu: Any) -> dict[str, Any]:
    is_image = isinstance(hdu, _FITS_IMAGE_HDU_TYPES)
    shape = tuple(int(n) for n in hdu.shape) if is_image else None
    has_data = bool(shape) and int(np.prod(shape)) > 0
    non_singleton = sum(1 for n in shape if n > 1) if shape else 0
    return {
        "index": index,
        "name": hdu.name,
        "type": type(hdu).__name__,
        "shape": list(shape) if shape is not None else None,
        "has_image_data": has_data,
        "usable": has_data and non_singleton == 2,
    }


def _select_hdu(hdus: list[dict[str, Any]], requested: int | None, path: Path) -> int:
    """Pick the HDU to load.

    Strategy (deterministic): an explicitly requested index wins; otherwise the first HDU,
    in file order, whose data is 2-D after dropping length-1 axes. Cubes and other
    higher-dimensional data are rejected rather than guessed at.
    """
    if requested is not None:
        if requested >= len(hdus):
            raise NoImageDataError(
                f"HDU {requested} requested but {path} has only {len(hdus)} HDU(s)"
            )
        chosen = hdus[requested]
        if chosen["usable"]:
            return requested
        if chosen["has_image_data"]:
            raise UnsupportedDimensionsError(
                f"HDU {requested} of {path} has image shape {tuple(chosen['shape'])}; "
                "only 2-D images are supported"
            )
        raise NoImageDataError(f"HDU {requested} ({chosen['type']}) of {path} has no image data")

    for hdu in hdus:
        if hdu["usable"]:
            return int(hdu["index"])

    other = [h for h in hdus if h["has_image_data"]]
    if other:
        shapes = ", ".join(f"HDU {h['index']}: {tuple(h['shape'])}" for h in other)
        raise UnsupportedDimensionsError(
            f"{path} has no 2-D image HDU ({shapes}); only 2-D images are supported. "
            "Data cubes and multi-plane colour FITS are not handled yet"
        )
    raise NoImageDataError(f"{path} contains no image data (HDUs: {len(hdus)})")


def _header_cards(header: fits.Header) -> list[list[Any]]:
    """Serialize a header as ``[keyword, value, comment]`` triples (keeps order/duplicates)."""
    return [[card.keyword, _card_value(card.value), card.comment] for card in header.cards]


def _card_value(value: Any) -> Any:
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else str(value)
    if isinstance(value, np.generic):
        return _card_value(value.item())
    return str(value)  # e.g. astropy Undefined, complex values


def _warn(collected: list[str], message: str) -> None:
    if message not in collected:
        collected.append(message)
        logger.warning(message)

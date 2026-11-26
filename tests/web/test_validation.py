"""Upload validation: names, sizes and file signatures (no analysis involved)."""

from __future__ import annotations

import gzip

import pytest

from astroidentify.web.validation import (
    UploadRejected,
    accepted_formats,
    check_content,
    check_request,
    display_name,
    extension_of,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32
FITS = b"SIMPLE  =                    T" + b" " * 50


def test_formats_match_the_loader():
    exts = {e for f in accepted_formats() for e in f["extensions"]}
    assert {".png", ".jpg", ".jpeg", ".fits", ".fit", ".fts", ".fits.gz"} <= exts
    assert [f["name"] for f in accepted_formats()] == ["JPEG", "PNG", "FITS"]


@pytest.mark.parametrize(
    ("name", "ext"),
    [("a.png", ".png"), ("A.JPG", ".jpg"), ("m.FITS.GZ", ".fits.gz"), ("x.fts", ".fts")],
)
def test_extension_of(name, ext):
    assert extension_of(name) == ext


def test_extension_of_unsupported():
    assert extension_of("notes.txt") is None
    assert extension_of("image.png.exe") is None


def test_display_name_never_keeps_paths_or_control_characters():
    assert display_name("../../etc/passwd.png") == "passwd.png"
    assert display_name("C:\\Users\\me\\m57.png") == "m57.png"
    assert display_name("bad\x00\x1bname.png") == "badname.png"
    assert display_name("") == "image"


def test_check_request_accepts_a_good_upload():
    assert check_request("m57.png", 1000, 2000) == ".png"


@pytest.mark.parametrize(
    ("name", "size", "status"),
    [
        (None, 10, 400),
        ("notes.txt", 10, 415),
        ("m57.png", None, 411),
        ("m57.png", 0, 400),
        ("m57.png", 2001, 413),
    ],
)
def test_check_request_rejections(name, size, status):
    with pytest.raises(UploadRejected) as info:
        check_request(name, size, 2000)
    assert info.value.status == status
    assert str(info.value)  # a message for the visitor


@pytest.mark.parametrize(("ext", "data"), [(".png", PNG), (".jpg", JPEG), (".fits", FITS)])
def test_check_content_accepts_matching_signatures(tmp_path, ext, data):
    path = tmp_path / f"input{ext}"
    path.write_bytes(data)
    check_content(path, ext)


def test_check_content_accepts_gzipped_fits(tmp_path):
    path = tmp_path / "input.fits.gz"
    path.write_bytes(gzip.compress(FITS))
    check_content(path, ".fits.gz")


@pytest.mark.parametrize(
    ("ext", "data"),
    [(".png", b"hello, not an image"), (".jpg", PNG), (".fits", PNG), (".fits.gz", PNG)],
)
def test_check_content_rejects_mismatched_content(tmp_path, ext, data):
    path = tmp_path / f"input{ext}"
    path.write_bytes(data)
    with pytest.raises(UploadRejected) as info:
        check_content(path, ext)
    assert info.value.status == 415

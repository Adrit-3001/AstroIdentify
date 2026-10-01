"""Fixtures for plate-solving tests: synthetic WCS truth and a fake ``solve-field``.

The fake solver is a real executable (run through the same subprocess boundary as
Astrometry.net) that mimics the conventions verified against Astrometry.net 0.93: it reads
FITS 1-based XYLS coordinates, writes a FITS WCS header, a correspondence table whose
``field_x``/``field_y`` repeat the source list, a match file and a ``--solved`` marker.
Its behaviour is chosen with ``FAKE_SOLVER_MODE`` (comma-separated: one mode per call).
"""

from __future__ import annotations

import stat
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astroidentify.config import DetectionConfig
from astroidentify.detection import detect_sources
from astroidentify.types import ImageFormat
from tests.detection.synthetic import Star, as_preprocessed, grid_stars, render_field

WIDTH, HEIGHT = 240, 200
TRUTH_CENTRE = (210.25, -35.5)  # arbitrary sky position (no real target implied)


def make_truth_wcs(
    width: int = WIDTH,
    height: int = HEIGHT,
    *,
    centre: tuple[float, float] = TRUTH_CENTRE,
    scale_arcsec: float = 1.5,
    rotation_deg: float = 25.0,
    camera_parity: bool = True,
) -> WCS:
    """TAN WCS whose reference pixel is the image centre (FITS 1-based CRPIX).

    ``camera_parity=True`` gives the orientation of a normal camera image displayed with
    row 0 at the top (north up, east left when rotation is 0): canonical +y is south.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = list(centre)
    wcs.wcs.crpix = [(width + 1) / 2.0, (height + 1) / 2.0]
    s = scale_arcsec / 3600.0
    t = np.radians(rotation_deg)
    # Columns: sky direction of +x and +y pixel steps in (east-ish, north) intermediate coords.
    cd = s * np.array([[-np.cos(t), np.sin(t)], [np.sin(t), np.cos(t)]])
    if camera_parity:
        cd[:, 1] *= -1.0  # +y (down the display) points south
    wcs.wcs.cd = cd
    return wcs


def write_header_file(wcs: WCS, path: Path, width: int = WIDTH, height: int = HEIGHT) -> Path:
    header = wcs.to_header()
    header["IMAGEW"] = width
    header["IMAGEH"] = height
    fits.PrimaryHDU(header=header).writeto(path, overwrite=True)
    return path


FAKE_SOLVER = textwrap.dedent(
    """\
    #!{python}
    import os, sys, time
    import numpy as np
    from astropy.io import fits
    from astropy.wcs import WCS

    args = sys.argv[1:]
    with open(os.environ["FAKE_SOLVER_ARGS_LOG"], "a") as fh:
        fh.write("\\x1f".join(args) + "\\n")
    if args == ["--version"]:
        print("This program is part of the Astrometry.net suite.")
        print("Revision 0.93-fake, date today")
        sys.exit(0)

    def opt(name):
        return args[args.index(name) + 1]

    # FAKE_SOLVER_MODE may be a comma-separated sequence: one mode per call (last repeats).
    modes = os.environ.get("FAKE_SOLVER_MODE", "solve").split(",")
    counter = os.environ["FAKE_SOLVER_ARGS_LOG"] + ".count"
    calls = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(calls + 1))
    mode = modes[min(calls, len(modes) - 1)]
    print("Reading input file 1 of 1:", args[0])
    if mode == "fail":
        print("engine.c: something broke", file=sys.stderr)
        sys.exit(3)
    if mode == "sleep":
        time.sleep(30)
    if mode == "unsolved":
        print("Did not solve (or no WCS file was written).")
        sys.exit(0)

    open(opt("--solved"), "wb").write(b"\\x01")
    if mode == "no_wcs":
        sys.exit(0)
    if mode == "bad_wcs":
        open(opt("--wcs"), "w").write("this is not FITS")
        sys.exit(0)

    truth_header = fits.getheader(os.environ["FAKE_SOLVER_TRUTH"])
    truth = WCS(truth_header)
    fits.PrimaryHDU(header=truth_header).writeto(opt("--wcs"), overwrite=True)
    xy = fits.getdata(args[0], 1)
    fx, fy = np.asarray(xy["X"], float), np.asarray(xy["Y"], float)
    n = min(len(fx), 12)  # pretend the brightest 12 stars matched
    fx, fy = fx[:n], fy[:n]
    ix, iy = fx + 0.1, fy - 0.05  # catalogue stars land slightly off the field stars
    fra, fdec = truth.all_pix2world(fx, fy, 1)  # XYLS coordinates are FITS 1-based
    ira, idec = truth.all_pix2world(ix, iy, 1)
    cols = [
        fits.Column(name=name, format="D", array=arr)
        for name, arr in [("field_x", fx), ("field_y", fy), ("field_ra", fra), ("field_dec", fdec),
                          ("index_x", ix), ("index_y", iy), ("index_ra", ira), ("index_dec", idec),
                          ("match_weight", np.full(n, 0.9))]
    ]
    fits.BinTableHDU.from_columns(cols).writeto(opt("--corr"), overwrite=True)
    match_cols = [
        fits.Column(name="LOGODDS", format="E", array=[123.4]),
        fits.Column(name="NMATCH", format="J", array=[n]),
        fits.Column(name="NDISTRACT", format="J", array=[2]),
        fits.Column(name="NCONFLICT", format="J", array=[0]),
        fits.Column(name="NFIELD", format="J", array=[len(xy)]),
        fits.Column(name="NINDEX", format="J", array=[n + 3]),
    ]
    fits.BinTableHDU.from_columns(match_cols).writeto(opt("--match"), overwrite=True)
    ra, dec = truth.wcs.crval
    print(f"  log-odds ratio 123.4 (1e53), {{n}} match, 0 conflict, 2 distractors.")
    print(f"Field center: (RA,Dec) = ({{ra:.6f}}, {{dec:.6f}}) deg.")
    print("Field size: 6 x 5 arcminutes")
    print("Field rotation angle: up is 155 degrees E of N")
    print("Field parity: neg")
    """
)


@pytest.fixture
def fake_solver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Install a fake solve-field; returns a setter ``(mode) -> executable path``."""
    script = tmp_path / "bin" / "solve-field"
    script.parent.mkdir()
    script.write_text(FAKE_SOLVER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    args_log = tmp_path / "solver-args.log"
    truth = write_header_file(make_truth_wcs(), tmp_path / "truth.wcs")
    monkeypatch.setenv("FAKE_SOLVER_ARGS_LOG", str(args_log))
    monkeypatch.setenv("FAKE_SOLVER_TRUTH", str(truth))

    def configure(mode: str = "solve") -> Path:
        monkeypatch.setenv("FAKE_SOLVER_MODE", mode)
        return script

    return configure


@pytest.fixture
def solver_calls(tmp_path: Path) -> Callable[[], list[list[str]]]:
    """Argument lists of all fake-solver invocations (excluding --version)."""

    def read() -> list[list[str]]:
        path = tmp_path / "solver-args.log"
        if not path.exists():
            return []
        calls = [line.split("\x1f") for line in path.read_text().splitlines()]
        return [c for c in calls if c != ["--version"]]

    return read


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    """A directory containing a (dummy) index file so prerequisite checks pass."""
    directory = tmp_path / "indexes"
    directory.mkdir()
    (directory / "index-9999.fits").write_bytes(b"dummy")
    return directory


@pytest.fixture(scope="session")
def detection():
    """A small synthetic star field run through Milestones 1-2."""
    stars = grid_stars((HEIGHT, WIDTH), spacing=30, amplitude=700.0, fwhm=3.5, margin=15)
    data = render_field((HEIGHT, WIDTH), stars=stars)
    return detect_sources(as_preprocessed(data), DetectionConfig(fwhm=3.5))


@pytest.fixture(scope="session")
def saturated_detection():
    """A field whose brightest stars saturate an 8-bit image, plus many fainter stars."""
    rng = np.random.default_rng(7)
    shape = (240, 320)
    bright = [Star(float(x), float(y), 4000.0, 4.0) for x, y in rng.uniform(30, 210, (6, 2))]
    faint = grid_stars(shape, spacing=24, amplitude=120.0, fwhm=4.0, margin=14)
    data = np.clip(render_field(shape, background=20.0, noise=2.0, stars=bright + faint), 0, 255)
    preprocessed = as_preprocessed(
        np.round(data).astype(np.uint8), image_format=ImageFormat.PNG, nominal_max=255
    )
    return detect_sources(preprocessed, DetectionConfig(fwhm=4.0))

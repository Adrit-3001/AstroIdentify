"""WCS loading, pixel <-> sky conversion and derived field geometry.

All pixel <-> sky conversion in AstroIdentify goes through :func:`pixel_to_sky` and
:func:`sky_to_pixel`. They take canonical pixel coordinates (0-based, pixel centres) and
call Astropy with ``origin=0``. FITS WCS headers themselves use 1-based ``CRPIX``;
Astropy's ``origin`` argument handles that, so no manual +/-1 appears here. The
``all_*`` methods include SIP distortion, which Astrometry.net solutions carry.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.utils.exceptions import AstropyWarning
from astropy.wcs import WCS, FITSFixedWarning

from astroidentify.astrometry.types import SkyPosition, WcsGeometry
from astroidentify.exceptions import InvalidWCSError

#: Astropy ``origin`` for canonical (0-based) pixel coordinates.
CANONICAL_ORIGIN = 0


def load_wcs(path: Path) -> tuple[WCS, fits.Header]:
    """Read a WCS from a FITS header file (e.g. ``solve-field --wcs`` output).

    Raises:
        InvalidWCSError: Missing/unreadable file, or not a 2-D celestial WCS.
    """
    if not Path(path).is_file():
        raise InvalidWCSError(f"WCS file not found: {path}")
    try:
        header = fits.getheader(path)
    except (OSError, ValueError) as exc:
        raise InvalidWCSError(f"could not read WCS header from {path}: {exc}") from exc
    return wcs_from_header(header), header


def wcs_from_header(header: fits.Header) -> WCS:
    """Build and validate a celestial WCS from a header."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(header, relax=True)
    except (ValueError, KeyError, MemoryError, AstropyWarning) as exc:
        raise InvalidWCSError(f"malformed WCS header: {exc}") from exc
    if wcs.naxis != 2 or not wcs.has_celestial:
        raise InvalidWCSError(
            f"WCS is not a 2-D celestial transform (naxis={wcs.naxis}, ctype={list(wcs.wcs.ctype)})"
        )
    try:
        ra, dec = pixel_to_sky(wcs, np.array([0.0]), np.array([0.0]))
    except (ValueError, ArithmeticError) as exc:
        raise InvalidWCSError(f"WCS transform failed: {exc}") from exc
    if not (np.isfinite(ra).all() and np.isfinite(dec).all()):
        raise InvalidWCSError("WCS transform produced non-finite coordinates")
    return wcs


def pixel_to_sky(wcs: WCS, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Canonical pixel coordinates -> (RA, Dec) in degrees."""
    ra, dec = wcs.all_pix2world(np.asarray(x, float), np.asarray(y, float), CANONICAL_ORIGIN)
    return np.mod(ra, 360.0), dec


def sky_to_pixel(
    wcs: WCS, ra: np.ndarray, dec: np.ndarray, *, best_effort: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """(RA, Dec) in degrees -> canonical pixel coordinates.

    The SIP inverse is iterative and can diverge for positions far outside the image.
    ``best_effort=True`` returns Astropy's best estimate instead of raising (for drawing
    only, where far-off points are discarded anyway).
    """
    with warnings.catch_warnings():
        if best_effort:
            warnings.simplefilter("ignore", RuntimeWarning)
        x, y = wcs.all_world2pix(
            np.asarray(ra, float), np.asarray(dec, float), CANONICAL_ORIGIN, quiet=best_effort
        )
    return x, y


def describe_wcs(wcs: WCS, width: int, height: int) -> WcsGeometry:
    """Derive centre, corners, scale, field size, orientation and parity."""
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    # Outer corners are pixel edges (half a pixel beyond the corner pixel centres).
    corner_pixels = {
        "top_left": (-0.5, -0.5),
        "top_right": (width - 0.5, -0.5),
        "bottom_right": (width - 0.5, height - 0.5),
        "bottom_left": (-0.5, height - 0.5),
    }
    probes = {
        "centre": (cx, cy),
        **corner_pixels,
        "left": (-0.5, cy),
        "right": (width - 0.5, cy),
        "top": (cx, -0.5),
        "bottom": (cx, height - 0.5),
        "step_x": (cx + 1.0, cy),
        "step_up": (cx, cy - 1.0),  # displayed up = towards row 0
    }
    names = list(probes)
    ra, dec = pixel_to_sky(
        wcs, np.array([probes[n][0] for n in names]), np.array([probes[n][1] for n in names])
    )
    sky = {n: SkyCoord(ra[i], dec[i], unit="deg") for i, n in enumerate(names)}
    position = {n: SkyPosition(float(ra[i]), float(dec[i])) for i, n in enumerate(names)}

    centre = sky["centre"]
    scale_x = centre.separation(sky["step_x"]).arcsec
    scale_up = centre.separation(sky["step_up"]).arcsec
    up_pa = float(centre.position_angle(sky["step_up"]).deg) % 360.0
    right_pa = float(centre.position_angle(sky["step_x"]).deg) % 360.0
    # Seen from the ground with north up, east is to the left: going from "up" to "right"
    # is clockwise on the display, i.e. the PA decreases by ~90 degrees.
    turn = (right_pa - up_pa + 540.0) % 360.0 - 180.0
    parity = "normal" if turn < 0 else "mirrored"

    return WcsGeometry(
        centre=position["centre"],
        corners={name: position[name] for name in corner_pixels},
        pixel_scale_arcsec=float(np.sqrt(scale_x * scale_up)),
        pixel_scale_x_arcsec=float(scale_x),
        pixel_scale_y_arcsec=float(scale_up),
        field_width_deg=float(sky["left"].separation(sky["right"]).deg),
        field_height_deg=float(sky["top"].separation(sky["bottom"]).deg),
        up_position_angle_deg=up_pa,
        parity=parity,
    )


def format_ra(ra_deg: float) -> str:
    """RA as ``HHh MMm SS.SSs``."""
    return SkyCoord(ra_deg, 0.0, unit="deg").ra.to_string(
        unit="hourangle", sep="hms", precision=2, pad=True
    )


def format_dec(dec_deg: float) -> str:
    """Dec as ``+DDd MMm SS.Ss``."""
    return SkyCoord(0.0, dec_deg, unit="deg").dec.to_string(
        sep="dms", precision=1, alwayssign=True, pad=True
    )

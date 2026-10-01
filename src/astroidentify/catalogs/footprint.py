"""WCS validation, catalogue query region and projection into image pixels.

All WCS math goes through Milestone 3's ``astrometry.wcs`` (Astropy with ``origin=0``
and canonical pixel coordinates). The Astrometry.net ``+1`` source-list offset does not
apply here: it exists only for the solver's XYLS input.

Query strategy: a cone centred on the WCS centre whose radius reaches the farthest image
corner (plus a margin) is a safe superset of any rotated or parity-flipped footprint and
has no RA wrap-around issues. The exact footprint is then applied locally by projecting
every returned row through the WCS and keeping only those inside the image.
"""

from __future__ import annotations

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from astroidentify.astrometry.types import WcsGeometry
from astroidentify.astrometry.wcs import describe_wcs, pixel_to_sky, sky_to_pixel
from astroidentify.catalogs.types import QueryRegion
from astroidentify.exceptions import InvalidPlateSolutionError

#: Maximum pixel -> sky -> pixel error accepted when validating a WCS.
ROUND_TRIP_TOLERANCE_PX = 1e-3
#: A projected row is kept only if projecting it back reproduces its sky position this well.
PROJECTION_CHECK_ARCSEC = 1e-3


def validate_wcs(wcs: WCS, width: int, height: int) -> WcsGeometry:
    """Check the WCS is finite and invertible over the image; return its geometry.

    Raises:
        InvalidPlateSolutionError: Non-finite transforms or a round trip beyond tolerance.
    """
    if width < 1 or height < 1:
        raise InvalidPlateSolutionError(f"invalid image size {width} x {height}")
    gx, gy = np.meshgrid(np.linspace(-0.5, width - 0.5, 9), np.linspace(-0.5, height - 0.5, 9))
    try:
        ra, dec = pixel_to_sky(wcs, gx.ravel(), gy.ravel())
        x, y = sky_to_pixel(wcs, ra, dec)
    except (ValueError, ArithmeticError) as exc:
        raise InvalidPlateSolutionError(f"WCS transform failed: {exc}") from exc
    if not (np.isfinite(ra).all() and np.isfinite(dec).all()):
        raise InvalidPlateSolutionError("WCS gives non-finite sky coordinates inside the image")
    error = float(np.max(np.hypot(x - gx.ravel(), y - gy.ravel())))
    if not error <= ROUND_TRIP_TOLERANCE_PX:
        raise InvalidPlateSolutionError(
            f"WCS pixel->sky->pixel round trip error {error:.3g} px exceeds "
            f"{ROUND_TRIP_TOLERANCE_PX} px"
        )
    return describe_wcs(wcs, width, height)


def query_region(wcs: WCS, width: int, height: int, margin_arcsec: float) -> QueryRegion:
    """Cone covering the image: centre + farthest corner + margin."""
    geometry = describe_wcs(wcs, width, height)
    centre = SkyCoord(geometry.centre.ra_deg, geometry.centre.dec_deg, unit="deg")
    distances = {
        name: float(centre.separation(SkyCoord(c.ra_deg, c.dec_deg, unit="deg")).deg)
        for name, c in geometry.corners.items()
    }
    return QueryRegion(
        centre=geometry.centre,
        radius_deg=max(distances.values()) + margin_arcsec / 3600.0,
        corner_distances_deg=distances,
        margin_arcsec=margin_arcsec,
        image_width=width,
        image_height=height,
    )


def project_to_image(
    wcs: WCS, ra: np.ndarray, dec: np.ndarray, width: int, height: int, margin_px: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project sky positions to canonical pixels; return ``(x, y, in_image)``.

    ``in_image`` requires finite coordinates inside ``[-0.5 - m, W - 0.5 + m]`` (and the same
    for y) and that projecting back reproduces the sky position, which rejects any row
    where the iterative SIP inverse did not converge.
    """
    ra = np.asarray(ra, float)
    dec = np.asarray(dec, float)
    finite = np.isfinite(ra) & np.isfinite(dec)
    x = np.full(ra.shape, np.nan)
    y = np.full(ra.shape, np.nan)
    if finite.any():
        x[finite], y[finite] = sky_to_pixel(wcs, ra[finite], dec[finite], best_effort=True)
    inside = (
        np.isfinite(x)
        & np.isfinite(y)
        & (x >= -0.5 - margin_px)
        & (x <= width - 0.5 + margin_px)
        & (y >= -0.5 - margin_px)
        & (y <= height - 0.5 + margin_px)
    )
    if inside.any():
        back_ra, back_dec = pixel_to_sky(wcs, x[inside], y[inside])
        check = SkyCoord(back_ra, back_dec, unit="deg").separation(
            SkyCoord(ra[inside], dec[inside], unit="deg")
        )
        ok = check.arcsec <= PROJECTION_CHECK_ARCSEC
        indices = np.flatnonzero(inside)
        inside[indices[~ok]] = False
    return x, y, inside

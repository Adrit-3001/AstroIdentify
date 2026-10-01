"""Angular extent of catalogued objects: parsing, sky ellipses and image overlap.

SIMBAD convention (verified against its TAP schema): ``galdim_majaxis`` and
``galdim_minaxis`` are full axis lengths in **arcmin**, and ``galdim_angle`` is the
major-axis position angle in **degrees east of north**. That is the same convention as
Astropy's ``SkyCoord.directional_offset_by``, so the ellipse is built on the sky and then
projected through the WCS. Image rotation and parity are handled by the WCS alone, and
the outline is never drawn with an assumed orientation.

Reliability rules (``parse_extent``):

* major, minor and angle all present -> ellipse;
* major only (or no angle for an elongated object) -> circle of the major-axis diameter,
  a superset of the true footprint;
* no positive major axis -> no extent (point marker; nothing is invented).
"""

from __future__ import annotations

import math

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from astroidentify.astrometry.wcs import pixel_to_sky, sky_to_pixel
from astroidentify.objects.types import (
    EXTENT_CIRCLE,
    EXTENT_ELLIPSE,
    ObjectExtent,
)

#: Vertices of a drawn/tested outline.
OUTLINE_POINTS = 72
#: Samples per image edge when testing whether the image boundary enters an ellipse.
EDGE_SAMPLES = 64


def parse_extent(
    major: float | None, minor: float | None, angle: float | None, quality: str | None
) -> ObjectExtent:
    """Catalogued size -> :class:`ObjectExtent` (see the module docstring)."""
    if major is None or not math.isfinite(major) or major <= 0:
        return ObjectExtent(quality=quality)
    minor_ok = minor is not None and math.isfinite(minor) and 0 < minor <= major
    angle_ok = angle is not None and math.isfinite(angle)
    if minor_ok and (angle_ok or minor == major):
        return ObjectExtent(major, minor, float(angle) % 180.0 if angle_ok else 0.0,
                            quality, EXTENT_ELLIPSE)  # fmt: skip
    return ObjectExtent(major, minor if minor_ok else None, None, quality, EXTENT_CIRCLE)


def _radius_deg(extent: ObjectExtent, theta: np.ndarray) -> np.ndarray:
    """Ellipse radius at angle ``theta`` (radians) from the major axis."""
    a, b = extent.semi_major_deg, extent.semi_minor_deg
    return a * b / np.sqrt((b * np.cos(theta)) ** 2 + (a * np.sin(theta)) ** 2)


def sky_outline(
    ra: float, dec: float, extent: ObjectExtent, n: int = OUTLINE_POINTS
) -> tuple[np.ndarray, np.ndarray]:
    """RA/Dec (deg) of ``n`` points on the catalogued ellipse."""
    theta = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    centre = SkyCoord(ra, dec, unit="deg")
    points = centre.directional_offset_by(
        (extent.drawn_position_angle_deg + np.degrees(theta)) * u.deg,
        _radius_deg(extent, theta) * u.deg,
    )
    return points.ra.deg, points.dec.deg


def inside_sky_ellipse(
    ra0: float, dec0: float, extent: ObjectExtent, ra: np.ndarray, dec: np.ndarray
) -> np.ndarray:
    """Which sky positions lie inside the catalogued ellipse."""
    centre = SkyCoord(ra0, dec0, unit="deg")
    points = SkyCoord(ra, dec, unit="deg")
    separation = centre.separation(points).deg
    theta = np.radians(centre.position_angle(points).deg - extent.drawn_position_angle_deg)
    return separation <= _radius_deg(extent, theta)


def interior_samples(
    ra: float, dec: float, extent: ObjectExtent, rings: int = 12, per_ring: int = 36
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic, area-uniform sample of the ellipse interior (RA/Dec, deg)."""
    fractions = np.sqrt((np.arange(rings) + 0.5) / rings)  # equal-area rings
    theta = np.linspace(0.0, 2 * np.pi, per_ring, endpoint=False)
    f, t = np.meshgrid(fractions, theta)
    centre = SkyCoord(ra, dec, unit="deg")
    points = centre.directional_offset_by(
        (extent.drawn_position_angle_deg + np.degrees(t.ravel())) * u.deg,
        (f.ravel() * _radius_deg(extent, t.ravel())) * u.deg,
    )
    return points.ra.deg, points.dec.deg


def _boundary_samples(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Points along the four image edges (pixel-boundary coordinates)."""
    t = np.linspace(0.0, 1.0, EDGE_SAMPLES)
    xs, ys = -0.5 + t * width, -0.5 + t * height
    left, right = np.full_like(ys, -0.5), np.full_like(ys, width - 0.5)
    top, bottom = np.full_like(xs, -0.5), np.full_like(xs, height - 0.5)
    return np.concatenate([xs, xs, left, right]), np.concatenate([top, bottom, ys, ys])


def _in_image(x: np.ndarray, y: np.ndarray, width: int, height: int) -> np.ndarray:
    return (
        np.isfinite(x) & np.isfinite(y)
        & (x >= -0.5) & (x <= width - 0.5) & (y >= -0.5) & (y <= height - 0.5)
    )  # fmt: skip


def image_overlap(
    wcs: WCS, ra: float, dec: float, extent: ObjectExtent, width: int, height: int,
    centre_in_image: bool,
) -> tuple[bool, float | None, tuple[tuple[float, float], ...] | None]:  # fmt: skip
    """``(intersects, fraction_of_footprint_in_image, pixel_outline)`` for a sized object.

    The footprint intersects the image if its centre or any outline vertex projects inside
    the image, or any sampled point of the image boundary lies inside the sky ellipse
    (which catches a footprint that contains the whole frame). The fraction comes from an
    area-uniform interior sample.
    """
    if not extent.has_size:
        return centre_in_image, None, None
    outline_ra, outline_dec = sky_outline(ra, dec, extent)
    ox, oy = sky_to_pixel(wcs, outline_ra, outline_dec, best_effort=True)
    outline = tuple(
        (float(x), float(y))
        for x, y in zip(ox, oy, strict=True)
        if np.isfinite(x) and np.isfinite(y)
    )
    intersects = centre_in_image or bool(_in_image(ox, oy, width, height).any())
    if not intersects:
        bx, by = _boundary_samples(width, height)
        bra, bdec = pixel_to_sky(wcs, bx, by)
        intersects = bool(inside_sky_ellipse(ra, dec, extent, bra, bdec).any())
    fraction = None
    if intersects:
        sra, sdec = interior_samples(ra, dec, extent)
        sx, sy = sky_to_pixel(wcs, sra, sdec, best_effort=True)
        fraction = float(np.mean(_in_image(sx, sy, width, height)))
    return intersects, fraction, outline or None

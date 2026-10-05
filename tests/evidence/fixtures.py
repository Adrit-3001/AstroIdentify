"""Synthetic Milestone 5 object records, images and astrometry for offline evidence tests.

Pixel scale 1"/px, so arcsec and pixels coincide. All objects are invented.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from astroidentify.config import EvidenceConfig
from astroidentify.evidence.pipeline import assess_objects
from astroidentify.evidence.types import AstrometryEvidence
from tests.catalogs.fixtures import sources_at

W, H = 400, 300
SCALE = 1.0
NOISE = 2.0
FAR_DETECTION = (380.0, 280.0)  # an unrelated detection far from all test objects


def field(grade="precise", r50=1.0, n=200, epoch=False) -> AstrometryEvidence:
    return AstrometryEvidence(
        grade=grade,
        wcs_source="catalog_refined",
        wcs_refined=True,
        plate_solution_mode="blind",
        gaia_matches=n,
        gaia_match_fraction=0.9,
        gaia_median_residual_px=r50,
        gaia_rms_residual_px=None if r50 is None else 1.3 * r50,
        gaia_median_residual_arcsec=None if r50 is None else r50 * SCALE,
        plate_solver_median_residual_arcsec=None,
        plate_solver_matches=None,
        epoch_propagated=epoch,
        r50_px=r50,
        r50_arcsec=None if r50 is None else r50 * SCALE,
        r50_source=None if r50 is None else "field_gaia",
        n_local_matches=None,
        local_radius_px=None,
    )


def plane(seed=0, discs=()) -> np.ndarray:
    """Noise image plus uniform discs ``(x, y, radius, amplitude)``."""
    image = 10.0 + np.random.default_rng(seed).normal(0, NOISE, (H, W))
    yy, xx = np.mgrid[:H, :W]
    for x, y, r, a in discs:
        image[np.hypot(xx - x, yy - y) <= r] += a
    return image.astype(np.float32)


def circle(x, y, r, n=72):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [(float(x + r * math.sin(a)), float(y - r * math.cos(a))) for a in t]


def obj(
    oid,
    x,
    y,
    *,
    radius_px=None,
    otype="G",
    category="galaxy",
    fraction=None,
    candidate=False,
    centre_in=True,
    minor_px=None,
):
    """A retained Milestone 5 object record (radius ``None``: no catalogued size)."""
    if radius_px is None:
        extent = {
            "major_arcmin": None,
            "minor_arcmin": None,
            "position_angle_deg": None,
            "quality": None,
            "shape": "none",
        }
        footprint, fraction = None, (1.0 if centre_in else None)
    else:
        d = 2 * radius_px * SCALE / 60
        extent = {
            "major_arcmin": d,
            "minor_arcmin": d,
            "position_angle_deg": 0.0,
            "quality": "B",
            "shape": "ellipse",
        }
        footprint = circle(x, y, radius_px)
        if fraction is None:  # area fraction of the disc inside the image (dense sampling)
            g = np.linspace(-radius_px, radius_px, 201)
            gx, gy = np.meshgrid(g, g)
            disc = np.hypot(gx, gy) <= radius_px
            px, py = gx[disc] + x, gy[disc] + y
            fraction = float(
                np.mean((px >= -0.5) & (px <= W - 0.5) & (py >= -0.5) & (py <= H - 0.5))
            )
    return {
        "catalogue": "SIMBAD",
        "catalogue_id": oid,
        "main_id": f"OBJ {oid}",
        "display_name": f"OBJ {oid}",
        "aliases": [f"OBJ {oid}"],
        "object_type": otype,
        "object_type_description": None,
        "category": category,
        "candidate_type": candidate,
        "projected_x": float(x),
        "projected_y": float(y),
        "centre_in_image": centre_in,
        "footprint_fraction_in_image": fraction,
        "extent": extent,
        "footprint_px": footprint,
        "magnitudes": {},
        "redshift": None,
        "morphological_type": None,
    }


def detections(xy, **changes):
    sources = sources_at(np.array(xy, float).reshape(-1, 2))
    return [dataclasses.replace(s, snr=50.0, **changes) for s in sources]


def run(objects, *, astrometry=None, image=None, dets=None, config=None, gaia=None):
    astrometry = astrometry or field()
    return assess_objects(
        objects,
        field=astrometry,
        position_scale=lambda x, y: (astrometry.r50_px, astrometry.r50_source, None, None),
        plane=plane() if image is None else image,
        detections=dets,
        fwhm_px=3.0,
        gaia_for_detection=gaia or {},
        pixel_scale=SCALE,
        compact_max_arcsec=10.0,
        config=config or EvidenceConfig(),
    )


def level(objects, **kwargs) -> str:
    return run(objects, **kwargs)[0].support_level

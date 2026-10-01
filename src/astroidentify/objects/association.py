"""Image association: objective image evidence for retained catalogue objects.

Catalogue presence and image evidence are kept separate. An object whose WCS position
falls in the image is ``catalogued_in_field`` whatever this module finds.

* **Compact objects** (no catalogued size, or a major axis up to
  ``compact_max_diameter_arcsec``): the nearest accepted Milestone 2 detection within
  ``association_radius_arcsec`` (Milestone 4's Gaia match radius, consistent with the
  astrometric residuals). Distances use the detections' astrometric centroids. Ties go to
  the lower ``source_id``. No detection in the radius gives association ``none``.
* **Extended objects**: no forced point match. Two simple measurements instead:
  the number of accepted detections inside the catalogued footprint, and a local brightness
  contrast, ``(median inside the footprint - median in an annulus of 1.5-2.5x the
  footprint) / (1.4826 x MAD of the annulus)``, on the detection plane (channel mean of
  the original pixels). Both are interpretable raw numbers, not probabilities.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from astroidentify.detection.types import Source
from astroidentify.objects.types import (
    ASSOC_EXTENDED,
    ASSOC_NO_POINT_SOURCE,
    ASSOC_NOT_ATTEMPTED,
    ASSOC_POINT_SOURCE,
    CatalogObject,
    ObjectAssociation,
)

ANNULUS_INNER = 1.5
ANNULUS_OUTER = 2.5


def is_compact(obj: CatalogObject, compact_max_diameter_arcsec: float) -> bool:
    if not obj.extent.has_size:
        return True
    return (obj.extent.major_arcmin or 0.0) * 60.0 <= compact_max_diameter_arcsec


def associate_point_source(
    obj: CatalogObject,
    detections: list[Source] | None,
    pixel_scale_arcsec: float,
    radius_arcsec: float,
) -> ObjectAssociation:
    """Nearest accepted detection within ``radius_arcsec`` of a compact object."""
    if detections is None:
        return ObjectAssociation(ASSOC_NOT_ATTEMPTED, note="no detection catalogue supplied")
    if not obj.centre_in_image:
        return ObjectAssociation(ASSOC_NOT_ATTEMPTED, note="catalogue position outside the image")
    radius_px = radius_arcsec / pixel_scale_arcsec
    candidates = []
    for source in detections:
        x, y = source.astrometric_xy
        distance = float(np.hypot(x - obj.projected_x, y - obj.projected_y))
        if distance <= radius_px:
            candidates.append((distance, source.source_id))
    if not candidates:
        return ObjectAssociation(
            ASSOC_NO_POINT_SOURCE,
            note=f'no accepted detection within {radius_arcsec:g}" ({radius_px:.2f} px)',
        )
    distance, source_id = min(candidates)
    return ObjectAssociation(
        ASSOC_POINT_SOURCE,
        detection_source_id=int(source_id),
        separation_px=distance,
        separation_arcsec=distance * pixel_scale_arcsec,
        n_detections_within_radius=len(candidates),
    )


def extended_evidence(
    obj: CatalogObject,
    detections: list[Source] | None,
    plane: np.ndarray | None,
    min_pixels: int,
) -> ObjectAssociation:
    """Footprint detection count and local brightness contrast of an extended object."""
    if not obj.footprint_px:
        return ObjectAssociation(ASSOC_EXTENDED, note="footprint could not be projected")
    height, width = plane.shape if plane is not None else (None, None)
    n_inside = None
    if detections is not None:
        xy = np.array([s.astrometric_xy for s in detections], float).reshape(-1, 2)
        n_inside = int(points_in_polygon(xy, obj.footprint_px).sum())
    if plane is None:
        return ObjectAssociation(ASSOC_EXTENDED, detections_in_footprint=n_inside,
                                 note="no image plane supplied")  # fmt: skip

    cx, cy = obj.projected_x, obj.projected_y
    outer_polygon = _scaled(obj.footprint_px, cx, cy, ANNULUS_OUTER)
    # Work in the annulus bounding box (clipped to the image) rather than the whole frame.
    xs, ys = [p[0] for p in outer_polygon], [p[1] for p in outer_polygon]
    x0, x1 = max(0, int(np.floor(min(xs)))), min(width, int(np.ceil(max(xs))) + 1)
    y0, y1 = max(0, int(np.floor(min(ys)))), min(height, int(np.ceil(max(ys))) + 1)
    if x1 <= x0 or y1 <= y0:
        return ObjectAssociation(ASSOC_EXTENDED, detections_in_footprint=n_inside,
                                 note="footprint and annulus lie outside the image")  # fmt: skip
    box_w, box_h = x1 - x0, y1 - y0

    def local(polygon):
        return [(x - x0, y - y0) for x, y in polygon]

    plane = plane[y0:y1, x0:x1]
    inside = polygon_mask(local(obj.footprint_px), box_w, box_h)
    outer = polygon_mask(local(outer_polygon), box_w, box_h)
    inner = polygon_mask(local(_scaled(obj.footprint_px, cx, cy, ANNULUS_INNER)), box_w, box_h)
    annulus = outer & ~inner
    n_in, n_annulus = int(inside.sum()), int(annulus.sum())
    if n_in < min_pixels or n_annulus < min_pixels:
        return ObjectAssociation(
            ASSOC_EXTENDED,
            detections_in_footprint=n_inside,
            footprint_pixels=n_in,
            note=f"too few in-image pixels for the contrast ({n_in} footprint, "
            f"{n_annulus} annulus; need {min_pixels})",
        )
    inside_values, annulus_values = plane[inside], plane[annulus]
    inside_values = inside_values[np.isfinite(inside_values)]
    annulus_values = annulus_values[np.isfinite(annulus_values)]
    if inside_values.size < min_pixels or annulus_values.size < min_pixels:
        return ObjectAssociation(ASSOC_EXTENDED, detections_in_footprint=n_inside,
                                 footprint_pixels=n_in, note="too few valid pixels")  # fmt: skip
    footprint_median = float(np.median(inside_values))
    annulus_median = float(np.median(annulus_values))
    sigma = float(1.4826 * np.median(np.abs(annulus_values - annulus_median)))
    contrast = (footprint_median - annulus_median) / sigma if sigma > 0 else None
    return ObjectAssociation(
        ASSOC_EXTENDED,
        detections_in_footprint=n_inside,
        footprint_pixels=n_in,
        footprint_median=footprint_median,
        annulus_median=annulus_median,
        annulus_sigma=sigma,
        brightness_contrast=contrast,
        note="" if contrast is not None else "annulus has zero scatter",
    )


def polygon_mask(polygon, width: int, height: int) -> np.ndarray:
    """Pixels whose centres fall inside ``polygon`` (canonical pixel coordinates).

    Pillow addresses pixels by integer index, i.e. the same pixel-centre convention.
    """
    canvas = Image.new("1", (width, height), 0)
    ImageDraw.Draw(canvas).polygon([tuple(p) for p in polygon], fill=1, outline=1)
    return np.asarray(canvas, dtype=bool)


def points_in_polygon(xy: np.ndarray, polygon) -> np.ndarray:
    """Even-odd ray casting for many points against one polygon."""
    poly = np.asarray(polygon, float)
    x, y = xy[:, 0][:, None], xy[:, 1][:, None]
    x1, y1 = poly[:, 0][None, :], poly[:, 1][None, :]
    x2, y2 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    crosses = (y1 > y) != (y2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return (crosses & (x < x_at)).sum(axis=1) % 2 == 1


def _scaled(polygon, cx: float, cy: float, factor: float):
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in polygon]

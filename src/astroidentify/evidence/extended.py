"""Image evidence for extended objects: footprint visibility, contrast and structure position.

No point-source match is required, and no segmentation or ML is used. All measurements use
the Milestone 5 projected footprint and the original pixels (channel mean):

* **Visibility.** ``mostly_visible`` if at least ``visible_fraction_ok`` of the catalogued
  footprint is in the image. ``larger_than_frame`` if the centre is in the image but the
  footprint is mostly beyond it because it exceeds the frame. ``truncated`` if a fraction
  between ``visible_fraction_min`` and ``visible_fraction_ok`` is visible.
  ``mostly_outside`` (abstain) if the centre is off-image and less than
  ``visible_fraction_min`` is visible.
* **Contrast.** ``c = (median in footprint - median in a 1.5-2.5x annulus) / sigma``,
  where sigma = 1.4826 MAD of the annulus (the Milestone 5 measurement, recomputed here).
  It is in *per-pixel noise* units, i.e. visual prominence. Its statistical significance
  is ``z = (median_in - median_annulus) / (1.2533 sigma sqrt(1/N_in + 1/N_annulus))``, the
  standard error of a median. A grade needs both: strong c >= 5 and z >= 10; moderate
  c >= 2 and z >= 5; weak c >= 0.5 and z >= 3. Below that it is ``none`` (not visible
  against the local background). With too few in-image footprint/annulus pixels it is
  ``unavailable``.
* **Structure offset.** The light centroid of footprint pixels more than
  ``structure_threshold_sigma`` above the annulus median is compared with the centroid
  of the in-image part of the footprint (the catalogue centre when fully visible; a
  truncated object's visible light cannot be centred on an off-frame centre), as a
  fraction of the catalogue radius. Accepted point sources are masked as
  probable foreground stars, except one coinciding with the catalogue centre (within
  3 r50 or the "centred" fraction of the radius), which is consistent with the object.
  It is only measured when the contrast grade is at least moderate (otherwise the centroid
  is noise).
* **Placement.** ``r50 / catalogue radius``: whether the WCS places the footprint precisely
  relative to its size.

Detections inside the footprint are recorded as context only. They are dominated by
foreground stars, so they are not evidence for or against the object.
"""

from __future__ import annotations

import math

import numpy as np

from astroidentify.config import EvidenceConfig
from astroidentify.detection.types import Source
from astroidentify.evidence.types import (
    GRADE_MODERATE,
    GRADE_NONE,
    GRADE_STRONG,
    GRADE_UNAVAILABLE,
    GRADE_WEAK,
    LEVEL_MODERATE,
    LEVEL_WEAK,
    ExtendedImageEvidence,
)
from astroidentify.objects.association import (
    ANNULUS_INNER,
    ANNULUS_OUTER,
    points_in_polygon,
    polygon_mask,
)

VISIBLE = "mostly_visible"
TRUNCATED = "truncated"
LARGER_THAN_FRAME = "larger_than_frame"
MOSTLY_OUTSIDE = "mostly_outside"

MIN_REGION_PIXELS = 25


def visibility_class(
    centre_in_image: bool,
    fraction: float | None,
    diameter_px: float,
    width: int,
    height: int,
    config: EvidenceConfig,
) -> str:
    if fraction is None:
        return VISIBLE if centre_in_image else MOSTLY_OUTSIDE
    if fraction >= config.visible_fraction_ok:
        return VISIBLE
    if centre_in_image and diameter_px > min(width, height):
        return LARGER_THAN_FRAME
    if fraction >= config.visible_fraction_min or centre_in_image:
        return TRUNCATED
    return MOSTLY_OUTSIDE


def grade_contrast(contrast: float | None, significance: float | None, config) -> str:
    if contrast is None or significance is None:
        return GRADE_UNAVAILABLE
    if contrast >= config.contrast_strong and significance >= config.significance_strong:
        return GRADE_STRONG
    if contrast >= config.contrast_moderate and significance >= config.significance_moderate:
        return GRADE_MODERATE
    if contrast >= config.contrast_weak and significance >= config.significance_weak:
        return GRADE_WEAK
    return GRADE_NONE


def measure_footprint(
    plane: np.ndarray,
    polygon,
    centre: tuple[float, float],
    detections: list[Source] | None,
    fwhm_px: float | None,
    config: EvidenceConfig,
    keep_radius_px: float = 0.0,
) -> dict:
    """Contrast, significance and structure centroid of one footprint (``None`` if unmeasurable).

    Accepted detections are masked (``star_mask_fwhm`` x FWHM) before the structure centroid,
    except those within ``keep_radius_px`` of the catalogue centre: a source at the centre
    is consistent with the object itself (a galaxy nucleus, a nebula's central star), and
    masking it would remove the object's own light.
    """
    result = dict(
        footprint_pixels=None,
        annulus_pixels=None,
        footprint_median=None,
        annulus_median=None,
        annulus_sigma=None,
        contrast=None,
        contrast_significance=None,
        structure_pixels=None,
        structure_centroid=None,
        footprint_centroid=None,
    )
    if not polygon:
        return result
    height, width = plane.shape
    cx, cy = centre
    outer = [(cx + (x - cx) * ANNULUS_OUTER, cy + (y - cy) * ANNULUS_OUTER) for x, y in polygon]
    inner = [(cx + (x - cx) * ANNULUS_INNER, cy + (y - cy) * ANNULUS_INNER) for x, y in polygon]
    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
    x0, x1 = max(0, math.floor(min(xs))), min(width, math.ceil(max(xs)) + 1)
    y0, y1 = max(0, math.floor(min(ys))), min(height, math.ceil(max(ys)) + 1)
    if x1 <= x0 or y1 <= y0:
        return result

    def local(points):
        return [(x - x0, y - y0) for x, y in points]

    box = plane[y0:y1, x0:x1]
    inside = polygon_mask(local(polygon), x1 - x0, y1 - y0)
    annulus = polygon_mask(local(outer), x1 - x0, y1 - y0) & ~polygon_mask(
        local(inner), x1 - x0, y1 - y0
    )
    inside = inside & np.isfinite(box)
    annulus = annulus & np.isfinite(box)
    n_in, n_annulus = int(inside.sum()), int(annulus.sum())
    result.update(footprint_pixels=n_in, annulus_pixels=n_annulus)
    if n_in:
        rows, cols = np.nonzero(inside)
        # Where the object's light should be centred given the part that is in the image.
        result["footprint_centroid"] = (float(cols.mean() + x0), float(rows.mean() + y0))
    if n_in < MIN_REGION_PIXELS or n_annulus < MIN_REGION_PIXELS:
        return result
    median_in = float(np.median(box[inside]))
    median_annulus = float(np.median(box[annulus]))
    sigma = float(1.4826 * np.median(np.abs(box[annulus] - median_annulus)))
    result.update(footprint_median=median_in, annulus_median=median_annulus, annulus_sigma=sigma)
    if not sigma > 0:
        return result
    excess = median_in - median_annulus
    result["contrast"] = excess / sigma
    result["contrast_significance"] = excess / (
        1.2533 * sigma * math.sqrt(1.0 / n_in + 1.0 / n_annulus)
    )

    structure = inside & (box - median_annulus > config.structure_threshold_sigma * sigma)
    if detections and fwhm_px:
        radius = config.star_mask_fwhm * fwhm_px
        reach = math.ceil(radius)
        for source in detections:
            sx, sy = source.astrometric_xy
            if math.hypot(sx - cx, sy - cy) <= keep_radius_px:
                continue
            # Mask a disc around the star, touching only its own small window of the box.
            c0, c1 = max(x0, math.floor(sx) - reach), min(x1, math.ceil(sx) + reach + 1)
            r0, r1 = max(y0, math.floor(sy) - reach), min(y1, math.ceil(sy) + reach + 1)
            if c1 <= c0 or r1 <= r0:
                continue
            rows, cols = np.mgrid[r0:r1, c0:c1]
            structure[r0 - y0 : r1 - y0, c0 - x0 : c1 - x0] &= (
                np.hypot(cols - sx, rows - sy) > radius
            )
    n_structure = int(structure.sum())
    result["structure_pixels"] = n_structure
    if n_structure >= config.structure_min_pixels:
        weights = (box - median_annulus)[structure]
        rows, cols = np.nonzero(structure)
        result["structure_centroid"] = (
            float(np.sum((cols + x0) * weights) / weights.sum()),
            float(np.sum((rows + y0) * weights) / weights.sum()),
        )
    return result


def assess_extended(
    obj: dict,
    plane: np.ndarray,
    detections: list[Source] | None,
    fwhm_px: float | None,
    r50_px: float | None,
    pixel_scale: float,
    config: EvidenceConfig,
) -> tuple[ExtendedImageEvidence, list[str], list[tuple[str, str]]]:
    """Return ``(evidence, reason_codes, caps)`` for an extended object record."""
    height, width = plane.shape
    extent = obj["extent"]
    radius = (extent.get("major_arcmin") or 0.0) * 30.0 / pixel_scale  # semi-major, px
    fraction = obj.get("footprint_fraction_in_image")
    centre_in = bool(obj.get("centre_in_image"))
    visibility = visibility_class(centre_in, fraction, 2 * radius, width, height, config)
    codes: list[str] = []
    caps: list[tuple[str, str]] = []
    if visibility == VISIBLE:
        codes.append("EXTENT_MOSTLY_VISIBLE")
    elif visibility == TRUNCATED:
        codes.append("OBJECT_TRUNCATED")
        caps.append((LEVEL_MODERATE, "OBJECT_TRUNCATED"))
    elif visibility == LARGER_THAN_FRAME:
        codes.append("OBJECT_LARGER_THAN_FRAME")
        caps.append((LEVEL_MODERATE, "OBJECT_LARGER_THAN_FRAME"))
    else:
        codes.append("OBJECT_MOSTLY_OUTSIDE")

    placement = r50_px / radius if (r50_px is not None and radius > 0) else None
    if placement is None:
        codes.append("MISSING_POSITION_SCALE")
    elif placement > config.placement_max_ratio:
        codes.append("POSITION_UNCERTAIN_FOR_SIZE")
        caps.append((LEVEL_WEAK, "POSITION_UNCERTAIN_FOR_SIZE"))
    elif placement > config.placement_ok_ratio:
        codes.append("POSITION_UNCERTAIN_FOR_SIZE")
        caps.append((LEVEL_MODERATE, "POSITION_UNCERTAIN_FOR_SIZE"))

    keep = max(
        config.consistent_offset * (r50_px or 0.0), config.structure_centred_fraction * radius
    )
    m = measure_footprint(
        plane,
        obj.get("footprint_px"),
        (obj["projected_x"], obj["projected_y"]),
        detections,
        fwhm_px,
        config,
        keep,
    )
    grade = grade_contrast(m["contrast"], m["contrast_significance"], config)
    codes.append(
        {
            GRADE_STRONG: "EXTENDED_CONTRAST_STRONG",
            GRADE_MODERATE: "EXTENDED_CONTRAST_MODERATE",
            GRADE_WEAK: "EXTENDED_CONTRAST_WEAK",
            GRADE_NONE: "EXTENDED_NOT_DETECTED",
            GRADE_UNAVAILABLE: "EXTENDED_CONTRAST_UNAVAILABLE",
        }[grade]
    )

    offset_px = offset_fraction = None
    if (
        m["structure_centroid"] is not None
        and grade in (GRADE_STRONG, GRADE_MODERATE)
        and radius > 0
    ):
        sx, sy = m["structure_centroid"]
        fx, fy = m["footprint_centroid"]
        offset_px = math.hypot(sx - fx, sy - fy)
        offset_fraction = offset_px / radius
        if offset_fraction <= config.structure_centred_fraction:
            codes.append("STRUCTURE_CENTRED")
        elif offset_fraction <= config.structure_offset_fraction:
            codes.append("STRUCTURE_PARTLY_OFFSET")
            caps.append((LEVEL_MODERATE, "STRUCTURE_PARTLY_OFFSET"))
        else:
            codes.append("STRUCTURE_OFFSET")
            caps.append((LEVEL_WEAK, "STRUCTURE_OFFSET"))
    elif grade in (GRADE_STRONG, GRADE_MODERATE):
        codes.append("STRUCTURE_NOT_MEASURED")

    in_footprint = None
    if detections is not None and obj.get("footprint_px"):
        xy = np.array([s.astrometric_xy for s in detections], float).reshape(-1, 2)
        in_footprint = int(points_in_polygon(xy, obj["footprint_px"]).sum()) if len(xy) else 0
    evidence = ExtendedImageEvidence(
        centre_in_image=centre_in,
        visible_fraction=fraction,
        visibility=visibility,
        radius_px=radius,
        placement_ratio=placement,
        footprint_pixels=m["footprint_pixels"],
        annulus_pixels=m["annulus_pixels"],
        footprint_median=m["footprint_median"],
        annulus_median=m["annulus_median"],
        annulus_sigma=m["annulus_sigma"],
        contrast=m["contrast"],
        contrast_significance=m["contrast_significance"],
        structure_pixels=m["structure_pixels"],
        structure_offset_px=offset_px,
        structure_offset_fraction=offset_fraction,
        detections_in_footprint=in_footprint,
        grade=grade,
    )
    return evidence, codes, caps

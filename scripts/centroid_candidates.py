"""Candidate saturated-star centroid estimators (Milestone 4.1 diagnostics; NOT production).

Every estimator returns canonical ``(x, y)`` or ``None`` (failure -> caller falls back to the
current saturated-core centroid). Inputs are image data only: the background-subtracted
detection plane, the saturated-pixel mask, per-pixel saturation depth and the positions of
the other accepted detections (masked so neighbours do not bias the wings).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, optimize

from astroidentify.detection.types import DetectionResult, Source

MIN_PIXELS = 20


@dataclass
class CentroidContext:
    subtracted: np.ndarray
    rms: np.ndarray
    saturated: np.ndarray
    depth: np.ndarray  # number of saturated channels per pixel
    labels: np.ndarray
    near_rows: np.ndarray
    near_cols: np.ndarray
    fwhm: float
    others: np.ndarray  # (N, 3): x, y, source_id of accepted detections

    @classmethod
    def from_detection(cls, detection: DetectionResult, saturated: np.ndarray) -> CentroidContext:
        data = detection.preprocessing.image.data
        level = detection.saturation_level
        with np.errstate(invalid="ignore"):
            depth = (data >= level).sum(axis=2) if data.ndim == 3 else (data >= level).astype(int)
        labels, _ = ndimage.label(saturated, structure=np.ones((3, 3), bool))
        _, (rows, cols) = ndimage.distance_transform_edt(~saturated, return_indices=True)
        others = np.array([(s.x, s.y, s.source_id) for s in detection.sources if s.accepted], float)
        return cls(
            np.asarray(detection.background.subtracted, float),
            np.asarray(detection.background.rms, float),
            saturated,
            depth,
            labels,
            rows,
            cols,
            detection.fwhm.value,
            others,
        )

    def core(self, source: Source) -> np.ndarray:
        """Boolean mask of the saturated region the source sits on."""
        r = int(np.clip(round(source.y), 0, self.labels.shape[0] - 1))
        c = int(np.clip(round(source.x), 0, self.labels.shape[1] - 1))
        label = self.labels[self.near_rows[r, c], self.near_cols[r, c]]
        return self.labels == label


def _window(source: Source, ctx: CentroidContext, outer_fwhm: float):
    core_radius = math.sqrt(max(source.saturated_core_area, 1) / math.pi)
    half = math.ceil(core_radius + outer_fwhm * ctx.fwhm)
    h, w = ctx.subtracted.shape
    r0, r1 = max(0, round(source.y) - half), min(h, round(source.y) + half + 1)
    c0, c1 = max(0, round(source.x) - half), min(w, round(source.x) + half + 1)
    rows, cols = np.mgrid[r0:r1, c0:c1]
    return (slice(r0, r1), slice(c0, c1)), rows, cols, half, core_radius


def _wing_mask(source, ctx, window, rows, cols, half, dilation):
    """Usable (unsaturated, non-neighbour, inside-circle) pixels in the window."""
    saturated = ctx.saturated[window]
    if dilation:
        saturated = ndimage.binary_dilation(saturated, iterations=int(dilation))
    usable = ~saturated & (np.hypot(cols - source.x, rows - source.y) <= half)
    for x, y, sid in ctx.others:
        if (
            int(sid) != source.source_id
            and abs(x - source.x) <= half + ctx.fwhm
            and abs(y - source.y) <= half + ctx.fwhm
        ):
            usable &= np.hypot(cols - x, rows - y) > ctx.fwhm
    return usable


def current_core(source: Source, ctx: CentroidContext) -> tuple[float, float]:
    return source.x, source.y


def wing_centre_of_light(source, ctx, mask_dilation_px=2, outer_fwhm=2.0):
    window, rows, cols, half, _ = _window(source, ctx, outer_fwhm)
    usable = _wing_mask(source, ctx, window, rows, cols, half, mask_dilation_px)
    weight = np.clip(ctx.subtracted[window], 0, None) * usable
    if usable.sum() < MIN_PIXELS or weight.sum() <= 0:
        return None
    return float((cols * weight).sum() / weight.sum()), float((rows * weight).sum() / weight.sum())


def masked_gaussian_fit(source, ctx, mask_dilation_px=2, outer_fwhm=2.0):
    window, rows, cols, half, core_radius = _window(source, ctx, outer_fwhm)
    usable = _wing_mask(source, ctx, window, rows, cols, half, mask_dilation_px)
    if usable.sum() < MIN_PIXELS:
        return None
    x, y = cols[usable].astype(float), rows[usable].astype(float)
    z = ctx.subtracted[window][usable]
    scale = float(np.median(ctx.rms[window])) or 1.0
    sigma0 = core_radius + ctx.fwhm / 2.3548

    def model(p):
        amp, x0, y0, sx, sy, theta, bkg = p
        c, s = math.cos(theta), math.sin(theta)
        u = (x - x0) * c + (y - y0) * s
        v = -(x - x0) * s + (y - y0) * c
        return amp * np.exp(-0.5 * ((u / sx) ** 2 + (v / sy) ** 2)) + bkg

    p0 = [2 * max(z.max(), 1.0), source.x, source.y, sigma0, sigma0, 0.0, 0.0]
    lower = [0, source.x - half, source.y - half, 0.5, 0.5, -math.pi, -np.inf]
    upper = [np.inf, source.x + half, source.y + half, 4 * half, 4 * half, math.pi, np.inf]
    try:
        fit = optimize.least_squares(
            lambda p: (model(p) - z) / scale,
            p0,
            bounds=(lower, upper),
            loss="soft_l1",
            max_nfev=400,
        )
    except (ValueError, RuntimeError):
        return None
    if not fit.success:
        return None
    x0, y0 = fit.x[1], fit.x[2]
    if math.hypot(x0 - source.x, y0 - source.y) > core_radius + ctx.fwhm:
        return None
    return float(x0), float(y0)


def wing_symmetry(source, ctx, mask_dilation_px=2, outer_fwhm=2.0):
    window, rows, cols, half, core_radius = _window(source, ctx, outer_fwhm)
    usable = _wing_mask(source, ctx, window, rows, cols, half, mask_dilation_px)
    image = ctx.subtracted[window]
    r0, c0 = window[0].start, window[1].start
    ys, xs = rows[usable].astype(float), cols[usable].astype(float)
    values = image[usable]
    if len(values) < MIN_PIXELS:
        return None

    def cost(centre):
        qx, qy = 2 * centre[0] - xs, 2 * centre[1] - ys
        local = np.vstack([qy - r0, qx - c0])
        inside = (
            (local[0] >= 0)
            & (local[0] <= image.shape[0] - 1)
            & (local[1] >= 0)
            & (local[1] <= image.shape[1] - 1)
        )
        ok_mask = (
            ndimage.map_coordinates(usable.astype(float), local, order=0, mode="constant") > 0.5
        )
        ok = inside & ok_mask
        if ok.sum() < MIN_PIXELS:
            return 1e30
        reflected = ndimage.map_coordinates(image, local[:, ok], order=1, mode="nearest")
        return float(np.mean((values[ok] - reflected) ** 2))

    result = optimize.minimize(
        cost,
        [source.x, source.y],
        method="Nelder-Mead",
        options={"xatol": 0.01, "fatol": 1e-6, "maxiter": 400},
    )
    x0, y0 = result.x
    if not np.isfinite(result.fun) or result.fun >= 1e30:
        return None
    if math.hypot(x0 - source.x, y0 - source.y) > core_radius + ctx.fwhm:
        return None
    return float(x0), float(y0)


def saturation_depth_core(source, ctx):
    core = ctx.core(source)
    rows, cols = np.nonzero(core)
    if not len(rows):
        return None
    weight = ctx.depth[rows, cols].astype(float)
    return float((cols * weight).sum() / weight.sum()), float((rows * weight).sum() / weight.sum())


ESTIMATORS = {
    "current_core": current_core,
    "wing_centre_of_light": wing_centre_of_light,
    "masked_gaussian_fit": masked_gaussian_fit,
    "wing_symmetry": wing_symmetry,
    "saturation_depth_core": saturation_depth_core,
}


# --------------------------------------------------------------------------- method F
# Isophote-calibrated core: a saturated plateau is the PSF's isophote at the saturation level.
# On an asymmetric PSF the centroid of an isophote shifts with its size. The shift is measured
# on the image's own stacked unsaturated PSF (aligned on DAOFIND centroids, which define the
# astrometric convention) and subtracted from the plateau centroid.


@dataclass
class IsophoteCalibration:
    area: np.ndarray  # isophote areas (increasing)
    dx: np.ndarray  # isophote centroid minus PSF centre, x
    dy: np.ndarray
    n_stars: int


def build_isophote_calibration(
    detection: DetectionResult,
    ctx: CentroidContext,
    max_stars: int = 150,
    half_fwhm: float = 5.0,
    isolation_fwhm: float = 4.0,
) -> IsophoteCalibration | None:
    half = math.ceil(half_fwhm * ctx.fwhm)
    h, w = ctx.subtracted.shape
    accepted = [s for s in detection.sources if s.accepted]
    pool = [
        s
        for s in accepted
        if not s.saturated
        and not s.edge
        and s.centroid_method == "daofind"
        and half <= s.x < w - half - 1
        and half <= s.y < h - half - 1
    ]
    xy = np.array([(s.x, s.y) for s in accepted])
    stack = []
    for s in sorted(pool, key=lambda s: -s.flux):
        d = np.hypot(xy[:, 0] - s.x, xy[:, 1] - s.y)
        if np.sum(d < isolation_fwhm * ctx.fwhm) > 1:  # itself + a neighbour
            continue
        r, c = round(s.y), round(s.x)
        cut = ctx.subtracted[r - half - 1 : r + half + 2, c - half - 1 : c + half + 2]
        if ctx.saturated[r - half - 1 : r + half + 2, c - half - 1 : c + half + 2].any():
            continue
        aligned = ndimage.shift(cut, (r - s.y, c - s.x), order=3, mode="nearest")[1:-1, 1:-1]
        total = aligned.sum()
        if total > 0:
            stack.append(aligned / total)
        if len(stack) >= max_stars:
            break
    if len(stack) < 10:
        return None
    psf = np.median(np.array(stack), axis=0)  # centre at (half, half)
    peak = psf.max()
    areas, dxs, dys = [], [], []
    ys, xs = np.mgrid[: psf.shape[0], : psf.shape[1]]
    start = np.unravel_index(np.argmax(psf), psf.shape)
    for fraction in np.geomspace(0.95, 0.01, 80):
        labels, _ = ndimage.label(psf > fraction * peak)
        region = labels == labels[start]
        if region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any():
            break  # isophote reaches the stack border: no longer reliable
        areas.append(region.sum())
        dxs.append(xs[region].mean() - half)
        dys.append(ys[region].mean() - half)
    areas = np.array(areas, float)
    order = np.argsort(areas)
    return IsophoteCalibration(areas[order], np.array(dxs)[order], np.array(dys)[order], len(stack))


def isophote_corrected_core(
    source,
    ctx,
    calibration: IsophoteCalibration | None = None,
    max_extrapolation=1.0,
    anchor_small=False,
):
    if calibration is None:
        return None
    area = source.saturated_core_area
    if area > calibration.area[-1] * max_extrapolation:
        return None  # outside the calibrated isophote range: fall back
    dx = np.interp(area, calibration.area, calibration.dx)
    dy = np.interp(area, calibration.area, calibration.dy)
    if anchor_small:  # zero correction for the smallest (peak-like) isophote
        dx -= calibration.dx[0]
        dy -= calibration.dy[0]
    return float(source.x - dx), float(source.y - dy)


ESTIMATORS["isophote_corrected_core"] = isophote_corrected_core


def production_astrometric(source, ctx):
    """The production Milestone 2 astrometric centroid (``Source.astrometric_xy``)."""
    return source.astrometric_xy


ESTIMATORS["production_astrometric"] = production_astrometric

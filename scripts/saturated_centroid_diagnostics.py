"""Milestone 4.1 diagnostic: saturated-star centroid estimators evaluated against Gaia DR3.

NOT production code. Gaia is used here only as an offline astrometric reference.

Reference positions: Gaia DR3 rows (``outputs/m57-catalog/gaia_sources.csv``) projected
through the Milestone 4 catalogue-refined WCS (``refined_solution.wcs``), which was fitted
to unsaturated stars only, so saturated-star residuals are an independent test.

Held-out protocol: saturated sources are split deterministically by ``source_id`` parity.
Each method's (small) parameter grid is tuned on the calibration half (even IDs) by
minimising the median residual, and reported on the held-out half (odd IDs).

Outputs (``outputs/m57-centroid-diagnostics/``):
    centroid_table.csv          one row per accepted detection with a Gaia association
    method_evaluation.json      per-method calibration choice and held-out metrics
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from centroid_candidates import (  # noqa: E402
    ESTIMATORS,
    CentroidContext,
    build_isophote_calibration,
)

from astroidentify import preprocess_image  # noqa: E402
from astroidentify.astrometry.wcs import load_wcs  # noqa: E402
from astroidentify.catalogs.footprint import project_to_image  # noqa: E402
from astroidentify.detection import detect_sources  # noqa: E402
from astroidentify.detection.saturation import saturated_pixel_mask  # noqa: E402
from astroidentify.serialization import to_jsonable  # noqa: E402

IMAGE = ROOT / "data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png"
REFINED_WCS = ROOT / "outputs/m57-catalog/refined_solution.wcs"
GAIA_CSV = ROOT / "outputs/m57-catalog/gaia_sources.csv"
OUT = ROOT / "outputs/m57-centroid-diagnostics"

ASSOCIATION_MARGIN_PX = 10.0  # beyond the saturated-core radius
AMBIGUITY_MAG = 2.0  # reject if another Gaia star within the radius is < 2 mag fainter


def load_gaia(wcs, width, height):
    rows = list(csv.DictReader(GAIA_CSV.open()))
    ra = np.array([float(r["ra"]) for r in rows])
    dec = np.array([float(r["dec"]) for r in rows])
    g = np.array([float(r["phot_g_mean_mag"]) if r["phot_g_mean_mag"] else np.nan for r in rows])
    ids = np.array([int(r["source_id"]) for r in rows])
    x, y, inside = project_to_image(wcs, ra, dec, width, height, margin_px=5.0)
    keep = inside & np.isfinite(g)
    return ids[keep], x[keep], y[keep], g[keep]


def associate(source, gx, gy, gmag):
    """Brightest Gaia star near the detection; ``None`` if absent or ambiguous."""
    core_radius = math.sqrt(max(source.saturated_core_area, 1) / math.pi)
    radius = core_radius + ASSOCIATION_MARGIN_PX
    near = np.flatnonzero(np.hypot(gx - source.x, gy - source.y) <= radius)
    if not len(near):
        return None
    order = near[np.argsort(gmag[near])]
    if len(order) > 1 and gmag[order[1]] - gmag[order[0]] < AMBIGUITY_MAG:
        return None
    return int(order[0])


def metrics(residuals: np.ndarray, dxy: np.ndarray, scale: float, n_total: int, n_fail: int):
    if not len(residuals):
        return {"n": 0}
    return {
        "n": len(residuals),
        "median_px": float(np.median(residuals)),
        "rms_px": float(np.sqrt(np.mean(residuals**2))),
        "p90_px": float(np.percentile(residuals, 90)),
        "median_arcsec": float(np.median(residuals) * scale),
        "rms_arcsec": float(np.sqrt(np.mean(residuals**2)) * scale),
        "median_dx_px": float(np.median(dxy[:, 0])),
        "median_dy_px": float(np.median(dxy[:, 1])),
        "failure_rate": n_fail / n_total if n_total else None,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    preprocessing = preprocess_image(IMAGE)
    detection = detect_sources(preprocessing)
    height, width = detection.plane.shape
    wcs, _ = load_wcs(REFINED_WCS)
    scale = float(np.sqrt(np.abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600)
    gids, gx, gy, gmag = load_gaia(wcs, width, height)
    saturated = saturated_pixel_mask(preprocessing.image, detection.saturation_level)
    context = CentroidContext.from_detection(detection, saturated)
    # Image-internal calibration for method F (unsaturated stars only; no Gaia involved).
    iso_calibration = build_isophote_calibration(detection, context)
    calibration_ok = iso_calibration is not None
    if calibration_ok:
        c = iso_calibration
        print(
            f"isophote calibration: {c.n_stars} stars, areas {c.area[0]:.0f}-{c.area[-1]:.0f} px, "
            f"offsets at max area ({c.dx[-1]:+.2f},{c.dy[-1]:+.2f})"
        )
        for a in (2, 10, 30, 100, 300, 600, 900):
            print(
                f"   area {a:4d}: offset "
                f"({np.interp(a, c.area, c.dx):+.2f}, {np.interp(a, c.area, c.dy):+.2f}) px"
            )

    accepted = [s for s in detection.sources if s.accepted]
    table = []
    for s in accepted:
        k = associate(s, gx, gy, gmag)
        if k is None:
            continue
        table.append(
            {
                "source_id": s.source_id,
                "x": s.x,
                "y": s.y,
                "saturated": s.saturated,
                "saturated_core_area": s.saturated_core_area,
                "n_saturated_pixels": s.n_saturated_pixels,
                "centroid_method": s.centroid_method,
                "flux": s.flux,
                "peak": s.peak,
                "snr": s.snr,
                "fwhm": s.fwhm,
                "sharpness": s.sharpness,
                "roundness1": s.roundness1,
                "roundness2": s.roundness2,
                "edge": s.edge,
                "gaia_source_id": int(gids[k]),
                "gaia_g": float(gmag[k]),
                "gaia_x": float(gx[k]),
                "gaia_y": float(gy[k]),
                "dx_px": s.x - gx[k],
                "dy_px": s.y - gy[k],
                "residual_px": float(np.hypot(s.x - gx[k], s.y - gy[k])),
                "residual_arcsec": float(np.hypot(s.x - gx[k], s.y - gy[k]) * scale),
            }
        )
    with (OUT / "centroid_table.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)

    by_id = {s.source_id: s for s in accepted}
    sat_rows = [r for r in table if r["saturated"]]
    unsat_rows = [r for r in table if not r["saturated"]]
    report: dict = {
        "reference": "Gaia DR3 projected through outputs/m57-catalog/refined_solution.wcs",
        "pixel_scale_arcsec": scale,
        "accepted": len(accepted),
        "saturated_accepted": sum(s.saturated for s in accepted),
        "associated": len(table),
        "associated_saturated": len(sat_rows),
        "split": "calibration = even source_id, held-out = odd source_id",
        "baseline": {},
        "methods": {},
    }

    def population(rows):
        dxy = np.array([(r["dx_px"], r["dy_px"]) for r in rows])
        return metrics(np.hypot(dxy[:, 0], dxy[:, 1]), dxy, scale, len(rows), 0)

    report["baseline"]["unsaturated_all"] = population(unsat_rows)
    # Unsaturated sources must be unchanged by the production astrometric centroid.
    report["unsaturated_astrometric_max_shift_px"] = max(
        abs(by_id[r["source_id"]].astrometric_xy[0] - r["x"])
        + abs(by_id[r["source_id"]].astrometric_xy[1] - r["y"])
        for r in unsat_rows
    )
    report["production_astrometric_centroid"] = detection.diagnostics["astrometric_centroid"]
    report["baseline"]["saturated_all"] = population(sat_rows)
    for lo, hi in ((1, 30), (30, 100), (100, 300), (300, 100000)):
        rows = [r for r in sat_rows if lo <= r["saturated_core_area"] < hi]
        report["baseline"][f"saturated_core_{lo}_{hi}"] = population(rows) if rows else {"n": 0}

    calibration = [r for r in sat_rows if r["source_id"] % 2 == 0]
    held_out = [r for r in sat_rows if r["source_id"] % 2 == 1]

    def evaluate(method, params, rows):
        dxy, fails = [], 0
        for r in rows:
            extra = {"calibration": iso_calibration} if method == "isophote_corrected_core" else {}
            result = ESTIMATORS[method](by_id[r["source_id"]], context, **params, **extra)
            if result is None:
                fails += 1
                x, y = r["x"], r["y"]  # fallback = current centroid
            else:
                x, y = result
            dxy.append((x - r["gaia_x"], y - r["gaia_y"]))
        dxy = np.array(dxy)
        return metrics(np.hypot(dxy[:, 0], dxy[:, 1]), dxy, scale, len(rows), fails)

    for method, grid in GRIDS.items():
        candidates = [
            dict(zip(grid, values, strict=True)) for values in itertools.product(*grid.values())
        ]
        scored = [(evaluate(method, p, calibration), p) for p in candidates]
        best_cal, best = min(scored, key=lambda item: item[0]["median_px"])
        report["methods"][method] = {
            "chosen_params": best,
            "calibration": best_cal,
            "held_out": evaluate(method, best, held_out),
            "held_out_by_core": {
                f"{lo}-{hi}": evaluate(
                    method, best, [r for r in held_out if lo <= r["saturated_core_area"] < hi]
                )
                for lo, hi in ((1, 30), (30, 100), (100, 100000))
                if any(lo <= r["saturated_core_area"] < hi for r in held_out)
            },
        }
        print(f"{method:22s} params={best} held-out: {_fmt(report['methods'][method]['held_out'])}")

    (OUT / "method_evaluation.json").write_text(json.dumps(to_jsonable(report), indent=2))
    print("baseline unsaturated:", _fmt(report["baseline"]["unsaturated_all"]))
    print("baseline saturated:  ", _fmt(report["baseline"]["saturated_all"]))
    print(
        "unsaturated max |astrometric - detection| (L1):",
        report["unsaturated_astrometric_max_shift_px"],
    )
    summary = report["production_astrometric_centroid"]
    print(
        "production:",
        summary["note"],
        summary["accepted_by_method"],
        "median/max correction",
        summary["median_correction_px"],
        summary["max_correction_px"],
    )
    return 0


def _fmt(m):
    if not m.get("n"):
        return "n=0"
    fail = f" fail={m['failure_rate']:.0%}" if m.get("failure_rate") is not None else ""
    return (
        f"n={m['n']} med={m['median_px']:.2f}px rms={m['rms_px']:.2f} p90={m['p90_px']:.2f} "
        f"bias=({m['median_dx_px']:+.2f},{m['median_dy_px']:+.2f}){fail}"
    )


# Small, target-agnostic parameter grids (in units of the image FWHM / pixels).
GRIDS: dict[str, dict[str, list]] = {
    "current_core": {},
    "wing_centre_of_light": {"mask_dilation_px": [1, 2, 3], "outer_fwhm": [1.5, 2.5]},
    "masked_gaussian_fit": {"mask_dilation_px": [1, 2], "outer_fwhm": [1.5, 2.5]},
    "wing_symmetry": {"mask_dilation_px": [1, 2, 3], "outer_fwhm": [1.5, 2.5]},
    "saturation_depth_core": {},
    "isophote_corrected_core": {"max_extrapolation": [1.0, 1.5], "anchor_small": [False, True]},
    "production_astrometric": {},
}


if __name__ == "__main__":
    sys.exit(main())

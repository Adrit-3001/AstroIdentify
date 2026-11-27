"""From pipeline outputs to markers, a plain-language outcome and the annotated image.

``build_analysis`` gathers every candidate marker the pipeline supports into one analysis
document (``analysis.json``); ``view.make_view`` picks what a particular view shows and names
(display options: marker types, brightest only, particular objects). One view document
(``result.json`` for the default view) drives both the browser preview and the annotated PNG,
so the two always show the same markers and the same labels.

What gets a marker, and what may be named:

* ``detection`` - a point of light the detection step accepted (brightest first).
  Never named.
* ``star``      - a detection the catalogue step matched to a Gaia DR3 star. Named when the
  identification step's SIMBAD star rows give that Gaia star a name (common name,
  Bayer/Flamsteed, variable-star, HD or HIP), or by its Gaia DR3 ID when asked for.
* ``object``    - a catalogued non-stellar object the identification step placed in the image
  (galaxy, nebula, cluster, ...), with its evidence level when the assessment ran. Named only
  when the assessment found image support (strong, moderate or weak).

Objects the image doesn't show (or can't confirm) can still be placed when asked for: the
analysis keeps their catalogue position projected through the plate solution, with an accuracy
radius from how well that solution fits the matched Gaia stars (``position_accuracy``).

A star's SIMBAD row is the one listing its Gaia DR3 source ID; failing that, a named row
within 3″ of it (the inspection module's naming rule, with proper motion back to J2000).

Coordinates are image pixels in display orientation (row 0 at the top), with integer values
at pixel centres (the pipeline's canonical convention). FITS images, stored bottom row
first, are flipped to the usual display orientation together with their markers.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from astroidentify.astrometry.wcs import format_dec, format_ra, load_wcs
from astroidentify.exceptions import InvalidImageError, OutputError
from astroidentify.inspection.references import load_gaia, star_name
from astroidentify.objects.overlay import CATEGORY_COLORS, DEFAULT_COLOR
from astroidentify.preprocessing.preview import stretch_for_display
from astroidentify.types import AstronomyImage, ImageFormat
from astroidentify.web.view import (
    DETECTION_COLOR,
    LABELLED_SUPPORT,
    NAMED_STAR_COLOR,
    STAR_COLOR,
    SUPPORT_ORDER,
    ViewOptions,
    make_view,
)

#: Version of ``analysis.json``.
ANALYSIS_VERSION = 2
#: A named SIMBAD star row this close to a Gaia star (at J2000) names it.
NAME_MATCH_ARCSEC = 3.0
#: 95% radius / median radius of a circular Gaussian position error (Rayleigh distribution):
#: sqrt(ln 20 / ln 2).
R95_PER_R50 = math.sqrt(math.log(20) / math.log(2))
#: Why an object's position is only an estimate (it isn't seen in the image).
NOT_SEEN = {
    "catalogue-only": "not_visible",
    "insufficient": "unconfirmed",
    None: "not_assessed",
}

SOLVE_FAILURES = {
    "timeout": "Plate solving ran out of time before it found where the image points.",
    "budget_exhausted": "Plate solving ran out of time before it found where the image points.",
    "unsolved": (
        "The pattern of stars in this image didn't match the sky indexes installed on this "
        "computer. This can happen with very small or very wide fields of view, too few "
        "stars, trailed stars, or images that aren't of the night sky."
    ),
    "prerequisites_missing": (
        "The plate solver (Astrometry.net) or its index files aren't available on this computer."
    ),
    "solver_error": "The plate solver failed while working on this image.",
    "invalid_wcs": "The plate solver returned a solution that failed AstroIdentify's checks.",
}


@dataclass(frozen=True)
class StepOutcome:
    """How one pipeline step ended: ``ok`` and, if not, its error message."""

    ok: bool
    error: str | None = None


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def display_pixels(image: AstronomyImage) -> tuple[np.ndarray, bool]:
    """8-bit pixels as the visitor should see the original, and whether a stretch was used.

    8-bit JPEG/PNG files are shown exactly as stored. Deeper rasters and FITS data need a
    display stretch (the pipeline's own preview stretch).
    """
    raster = (image.metadata or {}).get("raster") or {}
    eight_bit = image.format in (ImageFormat.JPEG, ImageFormat.PNG) and (
        raster.get("nominal_max") == 255
    )
    if eight_bit:
        pixels = np.clip(np.round(np.nan_to_num(image.data)), 0, 255).astype(np.uint8)
        stretched = False
    else:
        pixels = stretch_for_display(image)
        stretched = True
    if image.display_origin == "lower":
        pixels = np.flipud(pixels)
    return np.ascontiguousarray(pixels), stretched


def marker_style(width: int, height: int) -> dict[str, float]:
    """Marker geometry in image pixels, shared by the preview and the PNG."""
    scale = min(width, height)
    return {
        "line": max(1.0, round(scale / 700, 1)),
        "detection_radius": max(3.0, round(scale / 220, 1)),
        "star_radius": max(4.0, round(scale / 150, 1)),
        "object_inner": max(4.0, round(scale / 110, 1)),
        "object_outer": max(10.0, round(scale / 40, 1)),
        "label_size": max(12.0, round(scale / 55)),
    }


def build_result(
    image: AstronomyImage,
    step_dirs: dict[str, Path],
    outcomes: dict[str, StepOutcome],
    *,
    filename: str,
    solve_total_timeout_seconds: float,
    options: ViewOptions | None = None,
) -> tuple[dict[str, Any], np.ndarray]:
    """The view document for ``options`` (default view) and the base display pixels."""
    analysis, pixels = build_analysis(
        image,
        step_dirs,
        outcomes,
        filename=filename,
        solve_total_timeout_seconds=solve_total_timeout_seconds,
    )
    return make_view(analysis, options), pixels


def build_analysis(
    image: AstronomyImage,
    step_dirs: dict[str, Path],
    outcomes: dict[str, StepOutcome],
    *,
    filename: str,
    solve_total_timeout_seconds: float,
) -> tuple[dict[str, Any], np.ndarray]:
    """Every candidate marker plus outcome and sky position, and the display pixels."""
    pixels, stretched = display_pixels(image)
    height, width = pixels.shape[:2]
    flip = image.display_origin == "lower"

    def fy(y: float) -> float:
        return (height - 1) - y if flip else y

    def ok(step: str) -> bool:
        return step in outcomes and outcomes[step].ok

    notes: list[str] = []
    if stretched:
        notes.append("The image is shown with a display stretch so faint detail is visible.")
    if flip:
        notes.append("FITS data is stored bottom row first; it is shown flipped, the usual way.")

    # Detections and their Gaia matches.
    detections: list[dict[str, Any]] = []
    if ok("detect"):
        sources = (_read_json(step_dirs["detect"] / "sources.json") or {}).get("sources", [])
        detections = sorted(
            (s for s in sources if s.get("accepted")),
            key=lambda s: -(s.get("flux") or 0.0),
        )
    matches: dict[int, dict[str, str]] = {}
    if ok("catalog-match"):
        with (step_dirs["catalog-match"] / "catalog_matches.csv").open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                matches[int(row["detection_source_id"])] = row

    # Identified objects, their evidence, and SIMBAD rows for matched stars.
    table: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    star_rows: dict[int, dict[str, Any]] = {}
    gaia_in_image: dict[str, list[float]] = {}
    assessed = False
    pixel_scale = None
    field_accuracy = _field_accuracy(step_dirs, ok("catalog-match"))
    if ok("identify"):
        table = (_read_json(step_dirs["identify"] / "catalog_objects.json") or {}).get(
            "objects", []
        )
        summary = _read_json(step_dirs["identify"] / "identification_summary.json") or {}
        pixel_scale = (summary.get("image") or {}).get("pixel_scale_arcsec")
        evidence = {}
        if ok("assess"):
            doc = _read_json(step_dirs["assess"] / "object_evidence.json") or {}
            evidence = {e["catalogue_id"]: e for e in doc.get("objects", [])}
            assessed = True
        if field_accuracy is not None and pixel_scale:
            field_accuracy["r95_arcsec"] = round(field_accuracy["r95_px"] * pixel_scale, 2)
        objects = _objects(table, evidence, width, height, fy, pixel_scale, field_accuracy)
        if matches:
            matched_ids = {int(m["gaia_source_id"]) for m in matches.values()}
            star_rows = _star_rows(
                table, summary, width, height, matched_ids, pixel_scale, step_dirs
            )
    if ok("catalog-match"):
        gaia_in_image = _gaia_in_image(step_dirs["catalog-match"], matches, fy)

    matched_rows = [s for s in detections if s["source_id"] in matches]
    matched_rows.sort(key=lambda s: _mag_key(_g_mag(matches[s["source_id"]])))
    stars = []
    for s in matched_rows:
        match = matches[s["source_id"]]
        gaia_id = int(match["gaia_source_id"])
        star = {
            "kind": "star",
            "x": round(float(s["x"]), 2),
            "y": round(fy(float(s["y"])), 2),
            "gaia_id": str(gaia_id),
            "g_mag": _round(_g_mag(match), 2),
        }
        row = star_rows.get(gaia_id)
        if row is not None:
            named = star_name(row)
            if named:
                star["name"] = named[0]
            star["main_id"] = row.get("main_id")
            star["aliases"] = _star_aliases(row)
            star["common_names"] = list(row.get("common_names") or ())
        stars.append(star)
    unmatched = [
        {"kind": "detection", "x": round(float(s["x"]), 2), "y": round(fy(float(s["y"])), 2)}
        for s in detections
        if s["source_id"] not in matches
    ]

    # Catalogue rows that are neither drawn nor a matched star's: kept only to explain why a
    # requested name isn't labelled (not detected, outside the image, a type not marked).
    drawn = {o["id"] for o in objects}
    matched_gaia = {int(s["gaia_id"]) for s in stars}
    used = {id(r) for r in star_rows.values()}
    lookup = [
        _lookup_row(r, fy, width, height, gaia_in_image)
        for r in table
        if r["catalogue_id"] not in drawn
        and id(r) not in used
        and not matched_gaia.intersection(_gaia_ids(r))
    ]

    totals = {
        "detections": len(detections),
        "matched_stars": len(matched_rows),
        "objects": len(objects),
    }
    sky = _sky(step_dirs.get("solve"))
    if sky is not None:
        sky["check"] = _sky_check(step_dirs, ok("catalog-match"))
        sky["confirmed"] = ok("catalog-match")
    outcome = _outcome(outcomes, totals, sky, step_dirs, solve_total_timeout_seconds)

    analysis = {
        "analysis_version": ANALYSIS_VERSION,
        "filename": filename,
        "image": {
            "width": width,
            "height": height,
            "display_stretch": stretched,
            "flipped_vertically": flip,
        },
        "outcome": outcome,
        "sky": sky,
        "totals": totals,
        "assessed": assessed,
        "style": marker_style(width, height),
        "notes": notes,
        "pool": {"stars": stars, "detections": unmatched, "objects": objects},
        "lookup": lookup,
        "gaia_in_image": gaia_in_image,
        "field_accuracy": field_accuracy,
    }
    return analysis, pixels


def _g_mag(match: dict[str, str]) -> float:
    try:
        return float(match.get("phot_g_mean_mag") or "nan")
    except ValueError:
        return math.nan


def _mag_key(value: float) -> tuple[bool, float]:
    """Sort key: brightest first, missing magnitudes last."""
    return (not math.isfinite(value), value if math.isfinite(value) else 0.0)


def _round(value: float, digits: int) -> float | None:
    return round(value, digits) if math.isfinite(value) else None


def _magnitude(row: dict[str, Any]) -> dict[str, Any] | None:
    """Catalogued visual magnitude: SIMBAD V, else B."""
    mags = row.get("magnitudes") or {}
    for band in ("V", "B"):
        value = mags.get(band)
        if isinstance(value, int | float) and math.isfinite(value):
            return {"band": band, "value": round(float(value), 2)}
    return None


def _field_accuracy(step_dirs: dict[str, Path], gaia_ok: bool) -> dict[str, Any] | None:
    """Position accuracy over the whole field: the Gaia median residual of the WCS."""
    if not gaia_ok:
        return None
    summary = (_read_json(step_dirs["catalog-match"] / "catalog_match_summary.json") or {}).get(
        "summary", {}
    )
    r50 = summary.get("median_residual_px")
    if not isinstance(r50, int | float) or not math.isfinite(r50):
        return None
    return {
        "r95_px": round(r50 * R95_PER_R50, 2),
        "r95_arcsec": None,
        "source": "field",
        "n_stars": summary.get("matches"),
    }


def position_accuracy(evidence: dict[str, Any], field: dict[str, Any] | None, pixel_scale):
    """95% accuracy radius of a projected catalogue position: from the evidence step's local
    Gaia residuals near the object when it has them, else the field's. This is the accuracy of
    the image-to-sky mapping only; the catalogue position's own error is not included."""
    astrometry = evidence.get("astrometry") or {}
    r50 = astrometry.get("r50_px")
    if isinstance(r50, int | float) and math.isfinite(r50):
        r95 = r50 * R95_PER_R50
        local = astrometry.get("r50_source") == "local_gaia"
        return {
            "r95_px": round(r95, 2),
            "r95_arcsec": round(r95 * pixel_scale, 2) if pixel_scale else None,
            "source": "local" if local else "field",
            "n_stars": astrometry.get("n_local_matches")
            if local
            else astrometry.get("gaia_matches"),
        }
    return dict(field) if field else None


def _objects(
    table, evidence, width, height, fy, pixel_scale=None, field_accuracy=None
) -> list[dict[str, Any]]:
    """Markers for the identified (retained) objects that can be drawn, best supported first."""
    rows = [r for r in table if r.get("retained")]

    def rank(r):
        level = (evidence.get(r["catalogue_id"]) or {}).get("support_level")
        order = SUPPORT_ORDER.index(level) if level in SUPPORT_ORDER else len(SUPPORT_ORDER)
        return (order, -(r.get("n_references") or 0), r.get("display_name") or "")

    markers = []
    for r in sorted(rows, key=rank):
        x, y = r.get("projected_x"), r.get("projected_y")
        footprint = [[round(px, 2), round(fy(py), 2)] for px, py in (r.get("footprint_px") or [])]
        centre_in = (
            x is not None
            and y is not None
            and -0.5 <= x <= width - 0.5
            and -0.5 <= y <= height - 0.5
        )
        if not centre_in and not footprint:
            continue  # nothing of it can be drawn inside the image
        ev = evidence.get(r["catalogue_id"]) or {}
        support = ev.get("support_level")
        markers.append(
            {
                "kind": "object",
                "id": r["catalogue_id"],
                "x": round(float(x), 2) if x is not None else None,
                "y": round(fy(float(y)), 2) if y is not None else None,
                "centre_in_image": centre_in,
                "name": r.get("display_name") or r.get("main_id"),
                "common_name": (r.get("common_names") or [None])[0],
                "type": r.get("object_type_description") or r.get("object_type") or "",
                "category": r.get("category"),
                "color": _hex(CATEGORY_COLORS.get(r.get("category"), DEFAULT_COLOR)),
                "footprint": footprint or None,
                "support": support,
                "explanation": ev.get("explanation"),
                "magnitude": _magnitude(r),
                # Named only when the image shows it (the evidence step's levels), unless an
                # estimated position is asked for: then ``not_seen`` says why it's estimated.
                "eligible": support in LABELLED_SUPPORT,
                "not_seen": None if support in LABELLED_SUPPORT else NOT_SEEN.get(support),
                "accuracy": position_accuracy(ev, field_accuracy, pixel_scale),
                "main_id": r.get("main_id"),
                "aliases": list(r.get("aliases") or ()),
                "common_names": list(r.get("common_names") or ()),
            }
        )
    return markers


def _gaia_ids(row: dict[str, Any]) -> list[int]:
    ids = []
    for alias in row.get("aliases") or ():
        if alias.startswith("Gaia DR3 ") and alias[9:].strip().isdigit():
            ids.append(int(alias[9:]))
    return ids


def _star_aliases(row: dict[str, Any]) -> list[str]:
    """SIMBAD identifiers of a star, plus ``* bet01 Cyg``/``V* IV Vul`` without the prefix."""
    aliases = list(row.get("aliases") or ())
    for alias in list(aliases):
        for prefix in ("* ", "V* "):
            if alias.startswith(prefix):
                aliases.append(alias[len(prefix) :])
    return aliases


def _star_rows(table, summary, width, height, matched_ids, pixel_scale, step_dirs):
    """SIMBAD star row for each Gaia-matched star that has one (see the module docstring)."""
    simbad_stars = [o for o in table if o.get("category") == "star"]
    rows: dict[int, dict[str, Any]] = {}
    for row in simbad_stars:
        for gaia_id in _gaia_ids(row):
            if gaia_id in matched_ids:
                rows.setdefault(gaia_id, row)
    # Named rows without a Gaia DR3 ID (often the brightest stars): by position.
    named = [
        r for r in simbad_stars
        if not _gaia_ids(r) and star_name(r) and math.isfinite(r.get("projected_x") or math.nan)
    ]  # fmt: skip
    remaining = matched_ids - rows.keys()
    wcs_path = Path((summary.get("wcs") or {}).get("path", ""))
    gaia_path = step_dirs["catalog-match"] / "gaia_sources.csv"
    if not named or not remaining or not pixel_scale:
        return rows
    if not wcs_path.is_file() or not gaia_path.is_file():
        return rows
    wcs, _ = load_wcs(wcs_path)
    gaia = load_gaia(gaia_path, wcs, width, height)
    radius_px = NAME_MATCH_ARCSEC / pixel_scale
    for k, gaia_id in enumerate(gaia.source_id.tolist()):
        if gaia_id not in remaining:
            continue
        nx = float(gaia.x_name[k]) if gaia.x_name is not None else float(gaia.x[k])
        ny = float(gaia.y_name[k]) if gaia.y_name is not None else float(gaia.y[k])
        best = min(named, key=lambda r: math.hypot(r["projected_x"] - nx, r["projected_y"] - ny))
        if math.hypot(best["projected_x"] - nx, best["projected_y"] - ny) <= radius_px:
            rows[gaia_id] = best
    return rows


def _gaia_in_image(catalog_dir: Path, matches: dict[int, dict[str, str]], fy) -> dict:
    """Gaia stars inside the image, bright enough for the catalogue step to try to match them
    (``eligible``), that no detection was matched to: ``{source_id: [x, y, G]}`` with the
    catalogue step's projected (epoch-propagated) positions in display orientation."""
    path = catalog_dir / "gaia_sources.csv"
    matched = {m["gaia_source_id"] for m in matches.values()}
    found = {}
    try:
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("in_image") != "true" or r.get("eligible") != "true":
                    continue
                if r["source_id"] in matched:
                    continue
                try:
                    x, y = float(r["x_px"]), float(r["y_px"])
                except (KeyError, TypeError, ValueError):
                    x = y = math.nan
                g = _g_mag(r)
                found[r["source_id"]] = [
                    _round(x, 2), _round(fy(y), 2) if math.isfinite(y) else None, _round(g, 2)
                ]  # fmt: skip
    except OSError:
        return {}
    return found


def _lookup_row(r: dict[str, Any], fy, width, height, gaia_in_image) -> dict[str, Any]:
    """A catalogue row that isn't drawn: kept to explain why a requested name isn't named,
    and with its projected position, to place it as an estimate when that is asked for. Stars
    use their Gaia position (propagated to the image epoch) when Gaia lists them."""
    star = r.get("category") == "star"
    aliases = _star_aliases(r) if star else list(r.get("aliases") or ())
    x, y = r.get("projected_x"), r.get("projected_y")
    x = float(x) if isinstance(x, int | float) and math.isfinite(x) else None
    y = float(y) if isinstance(y, int | float) and math.isfinite(y) else None
    position_source = "simbad"
    for gaia_id in _gaia_ids(r):
        gx, gy, _ = gaia_in_image.get(str(gaia_id)) or (None, None, None)
        if gx is not None and gy is not None:
            x, y, position_source = gx, gy, "gaia"
            break
    else:
        y = fy(y) if y is not None else None
    inside = x is not None and y is not None and -0.5 <= x <= width - 0.5
    inside = inside and -0.5 <= y <= height - 0.5
    return {
        "x": round(x, 2) if x is not None else None,
        "y": round(y, 2) if y is not None else None,
        "in_image": inside,
        "position_source": position_source,
        "magnitude": _magnitude(r),
        "display_name": r.get("display_name") or r.get("main_id"),
        "main_id": r.get("main_id"),
        "aliases": aliases,
        "common_names": list(r.get("common_names") or ()),
        "catalogue_id": r.get("catalogue_id"),
        "object_type": r.get("object_type"),
        "type": r.get("object_type_description") or r.get("object_type"),
        "category": r.get("category"),
        "status": r.get("status") or ("catalogued_in_field" if r.get("retained") else None),
    }


def _sky(solve_dir: Path | None) -> dict[str, Any] | None:
    solution = _read_json(solve_dir / "plate_solution.json") if solve_dir else None
    if not solution or not solution.get("solved"):
        return None
    g = solution.get("geometry") or {}
    centre = solution.get("centre") or g.get("centre") or {}
    return {
        "ra": format_ra(centre["ra_deg"]),
        "dec": format_dec(centre["dec_deg"]),
        "ra_deg": centre["ra_deg"],
        "dec_deg": centre["dec_deg"],
        "field_width_arcmin": g.get("field_width_arcmin"),
        "field_height_arcmin": g.get("field_height_arcmin"),
        "pixel_scale_arcsec": g.get("pixel_scale_arcsec"),
        "up_position_angle_deg": g.get("up_position_angle_deg"),
    }


def _sky_check(step_dirs: dict[str, Path], gaia_ok: bool) -> str:
    """How well the sky position is supported, in plain words (no invented thresholds)."""
    if gaia_ok:
        summary = (_read_json(step_dirs["catalog-match"] / "catalog_match_summary.json") or {}).get(
            "summary", {}
        )
        n, residual = summary.get("matches"), summary.get("median_residual_arcsec")
        if n:
            text = f"Confirmed by {n} stars matched to Gaia DR3"
            return text + (f" (typical offset {residual:.1f}″)." if residual is not None else ".")
    solution = _read_json(step_dirs["solve"] / "plate_solution.json") or {}
    stats = solution.get("match_statistics") or {}
    n, of = stats.get("n_matched"), stats.get("n_selected")
    detail = f" (it matched {n} of the {of} brightest detected stars)" if n and of else ""
    return (
        f"From the plate solver alone{detail}. It couldn't be checked against Gaia DR3 stars, so "
        "treat this position as unconfirmed."
    )


def _duration(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} seconds"
    return f"{seconds / 60:.0f} minutes"


def friendly_error(error: str | None) -> str | None:
    """A pipeline error message in plain words (catalogue/network failures especially)."""
    if not error:
        return None
    lower = error.lower()
    if "<!doctype html" in lower or "<html" in lower:
        return (
            "The catalogue service answered with a web page instead of data. It may be down or "
            "under maintenance; try again later."
        )
    if any(k in lower for k in ("name or service not known", "name resolution", "getaddrinfo")):
        return (
            "The catalogue server's address couldn't be looked up: no internet connection, or "
            "a DNS problem on this computer."
        )
    if "could not reach" in lower:
        return "The catalogue service couldn't be reached: " + error.split(":", 1)[-1].strip()
    if "time limit" in lower or "timed out" in lower:
        return "The catalogue service didn't answer in time."
    return error if len(error) <= 240 else error[:237] + "…"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _outcome(outcomes, counts, sky, step_dirs, solve_total_timeout_seconds) -> dict[str, Any]:
    """Plain-language summary of how far the analysis got and what the markers mean."""

    def failed(step: str) -> bool:
        return step in outcomes and not outcomes[step].ok

    def error(step: str) -> str | None:
        return outcomes[step].error if step in outcomes else None

    points = _plural(counts["detections"], "point") + " of light"
    if failed("detect"):
        return {
            "kind": "no_detections",
            "title": "No points of light could be measured",
            "message": "AstroIdentify couldn't find stars to work with in this image, so it "
            "can't tell where it points or what it shows.",
            "detail": friendly_error(error("detect")),
        }
    if failed("solve"):
        solution = _read_json(step_dirs["solve"] / "plate_solution.json") or {}
        status = solution.get("status")
        message = SOLVE_FAILURES.get(status) or "Plate solving didn't succeed for this image."
        if status in ("timeout", "budget_exhausted"):
            message += f" (Time limit: {_duration(solve_total_timeout_seconds)} in total.)"
        return {
            "kind": "not_solved",
            "title": "Couldn't work out where this image points",
            "message": message,
            "markers": f"The markers show {points} found in the image. Without knowing where "
            "the image points, none of them can be named.",
            "detail": friendly_error(solution.get("error") or error("solve")),
        }
    where = "AstroIdentify worked out where this image points"
    if failed("catalog-match"):
        return {
            "kind": "catalog_unavailable",
            "title": "Sky position found; catalogue lookup failed",
            "message": "The plate solver found a sky position for this image, but AstroIdentify "
            "couldn't get Gaia DR3 star data to check it, so stars and objects weren't matched. "
            "Try again later.",
            "markers": f"The markers show {points} found in the image, without names.",
            "detail": friendly_error(error("catalog-match")),
        }
    if failed("identify"):
        return {
            "kind": "objects_unavailable",
            "title": "Stars matched; object lookup failed",
            "message": f"{where} and matched {_plural(counts['matched_stars'], 'star')} to Gaia "
            "DR3, but couldn't look up catalogued objects (SIMBAD) for this field. Check the "
            "internet connection and try again.",
            "markers": "Matched stars and other detected points are marked. No objects or star "
            "names are shown, because the object catalogue couldn't be reached.",
            "detail": friendly_error(error("identify")),
        }
    if counts["objects"] == 0:
        return {
            "kind": "no_objects",
            "title": "No catalogued objects in this field",
            "message": f"{where} and matched {_plural(counts['matched_stars'], 'star')} to Gaia "
            "DR3, but no catalogued galaxies, nebulae or star clusters fall inside the frame.",
            "markers": "Matched stars and other detected points are marked.",
            "detail": None,
        }
    note = None
    if failed("assess"):
        note = "The evidence check didn't run, so objects aren't graded by image evidence."
    return {
        "kind": "identified",
        "title": f"Found {_plural(counts['objects'], 'catalogued object')}",
        "message": f"{where} and found these catalogued objects inside the frame. A name means "
        "the catalogue places that object at that spot; the evidence level says how clearly "
        "the image itself shows it.",
        "markers": None,
        "detail": note,
    }


# --------------------------------------------------------------------------- rendering


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # Pillow without FreeType: fixed bitmap font
        return ImageFont.load_default()


def label_text(marker: dict[str, Any]) -> tuple[str, str | None]:
    """``(first line, second line)`` of a marker's label; identical in the preview."""
    if marker["kind"] == "object":
        name = marker["name"]
        if marker.get("common_name"):
            name += f" ({marker['common_name']})"
        second = " · ".join(t for t in (marker.get("type"), marker.get("estimate_note")) if t)
        return name, second or None
    return marker.get("name") or "", None


def estimate_radius(marker: dict[str, Any], minimum: float) -> float:
    """Radius of an estimated position's dashed circle: its 95% accuracy, at least
    ``minimum`` (so it stays visible); identical in the preview."""
    r95 = (marker.get("accuracy") or {}).get("r95_px") or 0.0
    return max(float(r95), minimum)


def marker_box(marker: dict[str, Any], style: dict[str, float], width: int, height: int):
    """Bounding box ``(x0, y0, x1, y1)`` a label is placed beside."""
    if marker["kind"] == "object":
        if marker.get("footprint"):
            pts = [
                (x, y)
                for x, y in marker["footprint"]
                if -0.5 <= x <= width - 0.5 and -0.5 <= y <= height - 0.5
            ] or marker["footprint"]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return min(xs), min(ys), max(xs), max(ys)
        r = style["object_outer"]
        if marker.get("estimated"):
            r = estimate_radius(marker, style["object_outer"])
    else:
        r = style["star_radius"]
    return marker["x"] - r, marker["y"] - r, marker["x"] + r, marker["y"] + r


def label_candidates(box, size, gap):
    """Spots for a label of ``size`` beside ``box``, in order of preference: right, left,
    below, above, then right/left shifted up and down by one and two label heights."""
    tw, th = size
    x0, y0, x1, y1 = box
    cy = (y0 + y1) / 2 - th / 2
    cx = (x0 + x1) / 2 - tw / 2
    spots = [(x1 + gap, cy), (x0 - gap - tw, cy), (cx, y1 + gap), (cx, y0 - gap - th)]
    for k in (1, -1, 2, -2):
        dy = k * (th + gap)
        spots += [(x1 + gap, cy + dy), (x0 - gap - tw, cy + dy)]
    return spots


def place_label(box, size, placed, width, height, gap):
    """Rectangle ``(x0, y0, x1, y1)`` for a label: the first candidate spot (kept inside
    the image) that overlaps no earlier label, else the one overlapping least. Labels are
    never dropped, so the preview and the PNG label exactly the same markers. The browser
    preview uses the same rule (``web/src/analyze/labels.js``)."""
    tw, th = size
    best, best_overlap = None, math.inf
    for sx, sy in label_candidates(box, size, gap):
        sx = min(max(sx, 0), max(0, width - tw))
        sy = min(max(sy, 0), max(0, height - th))
        rect = (sx, sy, sx + tw, sy + th)
        overlap = sum(_overlap_area(rect, p) for p in placed)
        if overlap == 0:
            return rect
        if overlap < best_overlap:
            best, best_overlap = rect, overlap
    return best


def _overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def render_annotated(pixels: np.ndarray, result: dict[str, Any]) -> Image.Image:
    """Full-resolution annotated image: the base pixels plus every marker and label."""
    base = Image.fromarray(pixels).convert("RGBA")
    width, height = base.size
    style = result["style"]
    line = max(1, round(style["line"]))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def ring(x, y, r, color, alpha, w):
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(*color, alpha), width=w)

    markers = result["markers"]
    for m in markers:
        if m["kind"] == "detection":
            ring(m["x"], m["y"], style["detection_radius"], DETECTION_COLOR, 150, line)
    for m in markers:
        if m["kind"] == "star":
            color = NAMED_STAR_COLOR if m.get("name") else STAR_COLOR
            ring(m["x"], m["y"], style["star_radius"], color, 215, line)
    stroke = 2 * line + 1
    for m in markers:
        if m["kind"] != "object":
            continue
        color = _rgb(m["color"])
        if m.get("estimated"):
            # Not seen in the image: dashed, so it never looks like a detection.
            dash = 3 * stroke + 4
            if m.get("footprint"):
                pts = [tuple(p) for p in m["footprint"]]
                _dashed(draw, [*pts, pts[0]], (*color, 235), stroke, dash)
            if m.get("centre_in_image"):
                x, y = m["x"], m["y"]
                r = estimate_radius(m, style["object_outer"])
                steps = max(24, int(2 * math.pi * r / dash) // 2 * 2)
                circle = [
                    (x + r * math.cos(2 * math.pi * k / steps),
                     y + r * math.sin(2 * math.pi * k / steps))
                    for k in range(steps + 1)
                ]  # fmt: skip
                for k in range(0, steps, 2):
                    draw.line(circle[k : k + 2], fill=(*color, 235), width=stroke)
                d = max(1.5, stroke)
                draw.ellipse((x - d, y - d, x + d, y + d), fill=(*color, 255))
            continue
        if m.get("footprint"):
            pts = [tuple(p) for p in m["footprint"]]
            draw.line([*pts, pts[0]], fill=(*color, 255), width=stroke, joint="curve")
        if m.get("centre_in_image"):
            x, y = m["x"], m["y"]
            inner, outer = style["object_inner"], style["object_outer"]
            if m.get("footprint"):
                inner, outer = 0, inner  # small centre tick inside the outline
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                draw.line(
                    [(x + dx * inner, y + dy * inner), (x + dx * outer, y + dy * outer)],
                    fill=(*color, 255),
                    width=stroke,
                )
    image = Image.alpha_composite(base, overlay)

    # Labels last, on top, with a dark outline so they read on any background.
    draw = ImageDraw.Draw(image)
    font = _font(int(style["label_size"]))
    small = _font(max(10, int(style["label_size"] * 0.8)))
    gap = 2 * style["line"] + 4
    placed: list[tuple[float, float, float, float]] = []
    outline = max(2, line + 1)
    for m in [m for m in markers if m.get("label")]:
        first, second = label_text(m)
        b1 = draw.textbbox((0, 0), first, font=font, stroke_width=outline)
        w = b1[2] - b1[0]
        h = b1[3] - b1[1]
        b2 = None
        if second:
            b2 = draw.textbbox((0, 0), second, font=small, stroke_width=outline)
            w = max(w, b2[2] - b2[0])
            h += round(style["label_size"] * 0.25) + (b2[3] - b2[1])
        rect = place_label(marker_box(m, style, width, height), (w, h), placed, width, height, gap)
        placed.append(rect)
        color = _rgb(m["color"]) if m["kind"] == "object" else NAMED_STAR_COLOR
        draw.text(
            (rect[0] - b1[0], rect[1] - b1[1]),
            first,
            font=font,
            fill=color,
            stroke_width=outline,
            stroke_fill=(0, 0, 0),
        )
        if second and b2:
            y2 = rect[1] + (b1[3] - b1[1]) + round(style["label_size"] * 0.25)
            draw.text(
                (rect[0] - b2[0], y2 - b2[1]),
                second,
                font=small,
                fill=(205, 212, 228),
                stroke_width=outline,
                stroke_fill=(0, 0, 0),
            )
    return image.convert("RGB")


def _dashed(draw, points, fill, width: int, dash: float) -> None:
    """A dashed polyline: ``dash`` pixels on, ``dash`` off, along ``points``."""
    on, left = True, dash
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        length = math.hypot(x1 - x0, y1 - y0)
        t = 0.0
        while t < length:
            step = min(left, length - t)
            if on:
                a, b = t / length, (t + step) / length
                draw.line(
                    [(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                     (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                    fill=fill, width=width,
                )  # fmt: skip
            t += step
            left -= step
            if left <= 0:
                on, left = not on, dash


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# --------------------------------------------------------------------------- CLI


@dataclass(frozen=True)
class AnnotationPaths:
    image: Path
    summary: Path


def annotation_inputs(
    objects_dir: Path,
    *,
    evidence_dir: Path | None = None,
    detections: Path | None = None,
    astrometry_dir: Path | None = None,
    catalog_dir: Path | None = None,
) -> tuple[dict[str, Path], dict[str, StepOutcome]]:
    """Step directories for ``build_analysis`` from saved outputs (``astroidentify annotate``).

    Missing inputs default to those recorded in the identification summary. A step counts as
    done when its main product exists; without ``evidence_dir`` no object can be named.

    Raises:
        OutputError: ``objects_dir`` is not an identification output directory.
    """
    objects_dir = Path(objects_dir)
    summary = _read_json(objects_dir / "identification_summary.json")
    if summary is None or not (objects_dir / "catalog_objects.json").is_file():
        raise OutputError(
            f"{objects_dir} is not an 'identify' output directory "
            "(identification_summary.json and catalog_objects.json are needed)"
        )
    recorded = summary.get("inputs") or {}

    def recorded_dir(key: str) -> Path | None:
        return Path(recorded[key]).parent if recorded.get(key) else None

    if detections is not None and Path(detections).suffix == ".json":
        detections = Path(detections).parent
    dirs = {
        "detect": Path(detections) if detections else recorded_dir("detections"),
        "solve": Path(astrometry_dir) if astrometry_dir else recorded_dir("plate_solution"),
        "catalog-match": Path(catalog_dir) if catalog_dir else recorded_dir("catalog_summary"),
        "identify": objects_dir,
        "assess": Path(evidence_dir) if evidence_dir else None,
    }
    products = {
        "detect": "sources.json",
        "solve": "plate_solution.json",
        "catalog-match": "catalog_matches.csv",
        "identify": "catalog_objects.json",
        "assess": "object_evidence.json",
    }
    outcomes = {}
    for step, directory in dirs.items():
        if directory is None:
            continue
        product = directory / products[step]
        ok = product.is_file()
        if ok and step == "solve":
            ok = bool((_read_json(product) or {}).get("solved"))
        outcomes[step] = StepOutcome(ok, None if ok else f"{product} not found")
    return {k: v for k, v in dirs.items() if v is not None}, outcomes


def annotate_image(
    image: AstronomyImage,
    step_dirs: dict[str, Path],
    outcomes: dict[str, StepOutcome],
    output_dir: Path,
    options: ViewOptions | None = None,
) -> tuple[dict[str, Any], AnnotationPaths]:
    """Write ``annotated.png`` and ``annotation.json`` (the view: options, markers, answers).

    Raises:
        InvalidImageError: the image isn't the one the products were made from (size).
        OutputError: the outputs can't be written.
    """
    summary = _read_json(step_dirs["identify"] / "identification_summary.json") or {}
    size = summary.get("image") or {}
    if size.get("width") and (size["width"], size["height"]) != (image.width, image.height):
        raise InvalidImageError(
            f"the image is {image.width}x{image.height} pixels but the identification was "
            f"made from a {size['width']}x{size['height']} image"
        )
    analysis, pixels = build_analysis(
        image,
        step_dirs,
        outcomes,
        filename=Path(image.source_path).name,
        solve_total_timeout_seconds=0,
    )
    view = make_view(analysis, options)
    output_dir = Path(output_dir)
    paths = AnnotationPaths(output_dir / "annotated.png", output_dir / "annotation.json")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        render_annotated(pixels, view).save(paths.image)
        document = {
            "statement": "Display only: the options choose among saved pipeline results; nothing "
            "is re-measured. Objects are named only with image evidence (strong, moderate or "
            "weak), stars only when matched to a detection in Gaia DR3; with undetected objects "
            "included, the others are drawn dashed at their estimated positions (catalogue "
            "position through the plate solution) and marked as not seen.",
            "inputs": {k: str(v) for k, v in step_dirs.items()},
            "steps_available": {k: o.ok for k, o in outcomes.items()},
            **view,
            "artifacts": {"image": str(paths.image), "summary": str(paths.summary)},
        }
        paths.summary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"could not write annotation outputs to {output_dir}: {exc}") from exc
    return view, paths

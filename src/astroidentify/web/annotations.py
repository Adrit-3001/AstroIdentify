"""From pipeline outputs to markers, a plain-language outcome and the annotated image.

One marker list (``result.json``) drives both the browser preview and the downloadable
``annotated.png``, so the two always show the same markers and the same labels.

What gets a marker, and what gets a name:

* ``detection`` - a point of light the detection step accepted (brightest first, capped).
  Never named.
* ``star``      - a detection the catalogue step matched to a Gaia DR3 star. Named only when
  the identification step's SIMBAD star rows give that Gaia star a name (common name,
  Bayer/Flamsteed, variable-star, HD or HIP), using the inspection module's naming rule.
* ``object``    - a catalogued non-stellar object the identification step placed in the image
  (galaxy, nebula, cluster, ...), with its evidence level when the assessment ran.

Coordinates are image pixels in display orientation (row 0 at the top), with integer values
at pixel centres (the pipeline's canonical convention). FITS images, stored bottom row
first, are flipped to the usual display orientation together with their markers.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from astroidentify.astrometry.wcs import format_dec, format_ra, load_wcs
from astroidentify.inspection.references import load_gaia, select_reference_stars
from astroidentify.objects.overlay import CATEGORY_COLORS, DEFAULT_COLOR
from astroidentify.preprocessing.preview import stretch_for_display
from astroidentify.types import AstronomyImage, ImageFormat

#: Brightest unmatched detections marked (the rest are counted, not drawn).
MAX_DETECTION_MARKERS = 300
#: Brightest Gaia-matched stars marked.
MAX_STAR_MARKERS = 300
#: Catalogued objects that get a text label on the image (all are marked and listed).
MAX_OBJECT_LABELS = 12
#: Named bright stars labelled on the image.
MAX_STAR_LABELS = 8
#: Brightest Gaia stars considered for names.
STAR_NAME_CANDIDATES = 40

SUPPORT_ORDER = ("strong", "moderate", "weak", "catalogue-only", "insufficient")
#: Evidence levels whose objects are named on the image (the others are marked and listed).
LABELLED_SUPPORT = ("strong", "moderate", "weak")
SUPPORT_TEXT = {
    "strong": "Strong image evidence",
    "moderate": "Moderate image evidence",
    "weak": "Weak image evidence",
    "catalogue-only": "Catalogued here; not visible in the image",
    "insufficient": "Not enough information to judge",
}
DETECTION_COLOR = (150, 162, 190)
STAR_COLOR = (150, 205, 255)
NAMED_STAR_COLOR = (235, 240, 255)

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
) -> tuple[dict[str, Any], np.ndarray]:
    """The result document and the base display pixels (display orientation)."""
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

    # Identified objects, their evidence, and names for matched stars.
    objects: list[dict[str, Any]] = []
    star_names: dict[int, str] = {}
    pixel_scale = None
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
        objects = _objects(table, evidence, width, height, fy)
        if matches and pixel_scale:
            star_names = _star_names(table, summary, step_dirs, width, height, matches, pixel_scale)

    markers: list[dict[str, Any]] = []
    matched_rows = [s for s in detections if s["source_id"] in matches]
    matched_rows.sort(key=lambda s: _g_mag(matches[s["source_id"]]))
    for s in matched_rows[:MAX_STAR_MARKERS]:
        match = matches[s["source_id"]]
        gaia_id = int(match["gaia_source_id"])
        marker = {
            "kind": "star",
            "x": round(float(s["x"]), 2),
            "y": round(fy(float(s["y"])), 2),
            "gaia_id": str(gaia_id),
            "g_mag": _round(_g_mag(match), 2),
        }
        if gaia_id in star_names:
            marker["name"] = star_names[gaia_id]
        markers.append(marker)
    named = [m for m in markers if m.get("name")][:MAX_STAR_LABELS]
    for m in named:
        m["label"] = True
    unmatched = [s for s in detections if s["source_id"] not in matches]
    for s in unmatched[:MAX_DETECTION_MARKERS]:
        markers.append(
            {"kind": "detection", "x": round(float(s["x"]), 2), "y": round(fy(float(s["y"])), 2)}
        )
    markers.extend(objects)

    counts = {
        "detections": len(detections),
        "detections_marked": min(len(unmatched), MAX_DETECTION_MARKERS),
        "matched_stars": len(matched_rows),
        "stars_marked": min(len(matched_rows), MAX_STAR_MARKERS),
        "named_stars": len(named),
        "objects": len(objects),
        "objects_labelled": sum(1 for o in objects if o.get("label")),
    }
    sky = _sky(step_dirs.get("solve"))
    if sky is not None:
        sky["check"] = _sky_check(step_dirs, ok("catalog-match"))
        sky["confirmed"] = ok("catalog-match")
    outcome = _outcome(outcomes, counts, sky, step_dirs, solve_total_timeout_seconds)

    result = {
        "schema_version": 1,
        "filename": filename,
        "image": {
            "width": width,
            "height": height,
            "display_stretch": stretched,
            "flipped_vertically": flip,
        },
        "outcome": outcome,
        "sky": sky,
        "counts": counts,
        "style": marker_style(width, height),
        "legend": _legend(counts, objects),
        "markers": markers,
        "objects": [_object_summary(o) for o in objects],
        "notes": notes,
    }
    return result, pixels


def _g_mag(match: dict[str, str]) -> float:
    try:
        return float(match.get("phot_g_mean_mag") or "nan")
    except ValueError:
        return math.nan


def _round(value: float, digits: int) -> float | None:
    return round(value, digits) if math.isfinite(value) else None


def _objects(table, evidence, width, height, fy) -> list[dict[str, Any]]:
    """Markers for the identified (retained) objects, labelled best-supported first."""
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
                "support": ev.get("support_level"),
                "explanation": ev.get("explanation"),
            }
        )
    # Names on the image for objects the image shows (the assessment's evidence levels); the
    # rest are marked and listed. Without an assessment, the best-referenced objects.
    assessed = bool(evidence)
    shown = [m for m in markers if m["support"] in LABELLED_SUPPORT] if assessed else markers
    for m in shown[:MAX_OBJECT_LABELS]:
        m["label"] = True
    return markers


def _star_names(table, summary, step_dirs, width, height, matches, pixel_scale) -> dict[int, str]:
    """Names for Gaia stars that were matched to detections (inspection naming rule)."""
    wcs_path = Path((summary.get("wcs") or {}).get("path", ""))
    gaia_path = step_dirs["catalog-match"] / "gaia_sources.csv"
    if not wcs_path.is_file() or not gaia_path.is_file():
        return {}
    wcs, _ = load_wcs(wcs_path)
    gaia = load_gaia(gaia_path, wcs, width, height)
    simbad_stars = [o for o in table if o.get("category") == "star"]
    region = (-0.5, -0.5, width - 0.5, height - 0.5)
    stars = select_reference_stars(gaia, STAR_NAME_CANDIDATES, region, simbad_stars, pixel_scale)
    matched_ids = {int(m["gaia_source_id"]) for m in matches.values()}
    return {s.gaia_source_id: s.name for s in stars if s.name and s.gaia_source_id in matched_ids}


def _object_summary(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": o["id"],
        "name": o["name"],
        "common_name": o["common_name"],
        "type": o["type"],
        "color": o["color"],
        "support": o["support"],
        "support_text": SUPPORT_TEXT.get(o["support"]) if o["support"] else None,
        "explanation": o["explanation"],
        "labelled": bool(o.get("label")),
        "centre_in_image": o["centre_in_image"],
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


def _legend(counts: dict[str, int], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legend = []
    if counts["objects"]:
        legend.append(
            {
                "kind": "object",
                "label": "Catalogued object",
                "detail": "A galaxy, nebula or cluster the catalogue places here: outlined when "
                "its size is catalogued, a crosshair when it isn't. Named on the image when the "
                "image shows evidence of it; all are listed beside the image.",
                "colors": sorted({o["color"] for o in objects}),
            }
        )
    if counts["matched_stars"]:
        detail = "A detected point of light matched to a star in Gaia DR3."
        if counts["stars_marked"] < counts["matched_stars"]:
            detail += f" The brightest {counts['stars_marked']} are marked."
        if counts["named_stars"]:
            detail += " Named when the star has a catalogued name."
        legend.append({"kind": "star", "label": "Matched star", "detail": detail})
    if counts["detections_marked"]:
        unmatched = counts["detections"] - counts["matched_stars"]
        detail = "A point of light found in the image, not matched to a catalogue."
        if counts["detections_marked"] < unmatched:
            detail += f" The brightest {counts['detections_marked']} of {unmatched} are marked."
        legend.append({"kind": "detection", "label": "Detected point", "detail": detail})
    return legend


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
        return name, marker.get("type") or None
    return marker.get("name") or "", None


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


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

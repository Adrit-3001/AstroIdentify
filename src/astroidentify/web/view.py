"""Display options: which markers an annotated image shows and which of them are named.

The analysis (``annotations.build_analysis``) keeps every candidate marker: all Gaia-matched
stars, all unmatched detections and all identified objects. A *view* picks from it:

* ``types``     - which kinds of marker to show: object categories (``galaxy``, ``nebula``,
  ``planetary_nebula``, ...), ``star`` (detections matched to Gaia DR3) and ``detection``
  (other points of light). ``None`` shows every kind.
* ``brightest`` - name only the N brightest of what may be named, ranked by catalogued
  magnitude: Gaia G for stars; V, else B (SIMBAD), for other objects. Objects without a
  catalogued magnitude come after those with one, in the usual order. Stars without a
  catalogued name are named by their SIMBAD main ID (e.g. ``TYC 2642-385-1``), else their Gaia
  DR3 source ID; by default only stars with a name (common, Bayer/Flamsteed, variable, HD,
  HIP) are labelled.
* ``labels``    - names (display name, SIMBAD main ID, alias, common name, Gaia DR3 source
  ID) of particular objects to name in addition, even when their type is hidden. Matched
  exactly, ignoring case and spacing (``inspection.selection.find_record``).

What may be named never changes with the options: an object only when the evidence step found
image support for it (strong, moderate or weak), a star only when a detection was matched to it
in Gaia DR3. A request for anything else is answered with the reason it isn't named. The
options only choose among the pipeline's own results; nothing is re-measured or re-matched.

Without options the view is the default one: every type, up to ``MAX_OBJECT_LABELS`` objects
(best supported first) and ``MAX_STAR_LABELS`` named stars (brightest first).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from astroidentify.inspection.selection import ObjectSelectionError, find_record
from astroidentify.objects.filtering import ALL_CATEGORIES, CATEGORY_STAR
from astroidentify.objects.overlay import CATEGORY_COLORS, DEFAULT_COLOR

#: Brightest unmatched detections marked (the rest are counted, not drawn).
MAX_DETECTION_MARKERS = 300
#: Brightest Gaia-matched stars marked.
MAX_STAR_MARKERS = 300
#: Catalogued objects named on the image by default.
MAX_OBJECT_LABELS = 12
#: Named stars labelled on the image by default.
MAX_STAR_LABELS = 8
#: Limits on the options themselves.
MAX_BRIGHTEST = 100
MAX_REQUESTS = 20
MAX_REQUEST_LENGTH = 100

TYPE_DETECTION = "detection"
TYPE_KEYS: tuple[str, ...] = (*ALL_CATEGORIES, TYPE_DETECTION)
TYPE_LABELS = {
    "galaxy": "Galaxies",
    "galaxy_group": "Galaxy groups",
    "galaxy_part": "Parts of galaxies",
    "nebula": "Nebulae",
    "planetary_nebula": "Planetary nebulae",
    "supernova_remnant": "Supernova remnants",
    "star_cluster": "Star clusters",
    CATEGORY_STAR: "Stars",
    TYPE_DETECTION: "Other points of light",
}
#: Object categories in the order the options list them (stars and detections come last).
_TYPE_ORDER = (
    "galaxy",
    "galaxy_group",
    "galaxy_part",
    "nebula",
    "planetary_nebula",
    "supernova_remnant",
    "star_cluster",
)

SUPPORT_ORDER = ("strong", "moderate", "weak", "catalogue-only", "insufficient")
#: Evidence levels whose objects may be named on the image (the image shows them).
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

# Outcomes of a request to name a particular object.
REQUEST_LABELLED = "labelled"
REQUEST_NOT_DETECTED = "not_detected"  # in the image per the catalogue, but not detected
REQUEST_NOT_ANNOTATED = "not_annotated"  # in the field, but a kind AstroIdentify doesn't mark
REQUEST_OUTSIDE = "outside"
REQUEST_AMBIGUOUS = "ambiguous"
REQUEST_NOT_FOUND = "not_found"


class ViewOptionsError(ValueError):
    """Invalid display options (HTTP 400, CLI exit code 2)."""


def hex_color(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


@dataclass(frozen=True)
class ViewOptions:
    """Which markers to show and name (see the module docstring)."""

    types: frozenset[str] | None = None
    brightest: int | None = None
    labels: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        types: Iterable[str] | None = None,
        brightest: int | str | None = None,
        labels: Iterable[str] = (),
    ) -> ViewOptions:
        """Validated options from user input. ``types`` items may be comma-separated.

        Raises:
            ViewOptionsError: unknown type, bad count, too many or too long names.
        """
        chosen = None
        if types is not None:
            chosen = {t.strip() for item in types for t in str(item).split(",") if t.strip()}
            unknown = sorted(chosen - set(TYPE_KEYS))
            if unknown:
                raise ViewOptionsError(
                    f"unknown type(s) {', '.join(unknown)}; choose from {', '.join(TYPE_KEYS)}"
                )
            chosen = frozenset(chosen)
        count = None
        if brightest not in (None, ""):
            try:
                if isinstance(brightest, bool) or (
                    isinstance(brightest, float) and not brightest.is_integer()
                ):
                    raise ValueError(brightest)
                count = int(brightest)
            except (TypeError, ValueError):
                raise ViewOptionsError(
                    f"brightest must be a whole number, got {brightest!r}"
                ) from None
            if not 1 <= count <= MAX_BRIGHTEST:
                raise ViewOptionsError(f"brightest must be between 1 and {MAX_BRIGHTEST}")
        names: list[str] = []
        for name in labels:
            text = " ".join(str(name).split())
            if not text:
                continue
            if len(text) > MAX_REQUEST_LENGTH:
                raise ViewOptionsError(f"names are limited to {MAX_REQUEST_LENGTH} characters")
            if text.casefold() not in {n.casefold() for n in names}:
                names.append(text)
        if len(names) > MAX_REQUESTS:
            raise ViewOptionsError(f"at most {MAX_REQUESTS} objects can be named at once")
        return cls(chosen, count, tuple(names))

    @classmethod
    def from_query(cls, params: Mapping[str, list[str]]) -> ViewOptions:
        """Options from URL query parameters ``types`` (comma list; present but empty means
        none), ``brightest`` and ``label`` (repeated). Other parameters are ignored."""
        brightest = (params.get("brightest") or [None])[-1]
        return cls.from_values(params.get("types"), brightest, params.get("label") or ())

    @property
    def is_default(self) -> bool:
        return self.types is None and self.brightest is None and not self.labels

    def shows(self, key: str) -> bool:
        return self.types is None or key in self.types

    def to_dict(self) -> dict[str, Any]:
        return {
            "types": sorted(self.types, key=TYPE_KEYS.index) if self.types is not None else None,
            "brightest": self.brightest,
            "labels": list(self.labels),
        }


def make_view(analysis: dict[str, Any], options: ViewOptions | None = None) -> dict[str, Any]:
    """The result document for ``options``: markers (with ``label`` flags), counts, legend,
    object list, and how each named request was answered. One document drives both the
    browser preview and the annotated PNG."""
    options = options or ViewOptions()
    pool = analysis["pool"]
    stars, detections, objects = pool["stars"], pool["detections"], pool["objects"]

    requests, wanted = _resolve_requests(analysis, options.labels)

    shown_objects = [o for o in objects if options.shows(o["category"]) or o["id"] in wanted]
    shown_stars = []
    if options.shows(CATEGORY_STAR):
        shown_stars = stars[:MAX_STAR_MARKERS]
    extra = [s for s in stars if _star_key(s) in wanted and s not in shown_stars]
    shown_stars = sorted([*shown_stars, *extra], key=_star_order)
    shown_detections = detections[:MAX_DETECTION_MARKERS] if options.shows(TYPE_DETECTION) else []

    # What gets a name: the requests, plus either the default caps or the N brightest.
    labelled: set = set(wanted)
    eligible_objects = [o for o in shown_objects if o["eligible"] and options.shows(o["category"])]
    named_stars = [s for s in shown_stars if s.get("name") and options.shows(CATEGORY_STAR)]
    if options.brightest is None:
        labelled |= {o["id"] for o in eligible_objects[:MAX_OBJECT_LABELS]}
        labelled |= {_star_key(s) for s in named_stars[:MAX_STAR_LABELS]}
    else:
        # Every matched star was detected, so all may be named here: unnamed ones by their
        # catalogue ID (many bright stars have only a TYC or Gaia identifier).
        star_candidates = shown_stars if options.shows(CATEGORY_STAR) else []
        ranked = sorted(
            [*(("object", i, o) for i, o in enumerate(eligible_objects)),
             *(("star", i, s) for i, s in enumerate(star_candidates))],
            key=lambda item: _brightness_rank(*item),
        )  # fmt: skip
        for kind, _, m in ranked[: options.brightest]:
            labelled.add(m["id"] if kind == "object" else _star_key(m))

    markers: list[dict[str, Any]] = []
    for s in shown_stars:
        marker = {k: s[k] for k in ("kind", "x", "y", "gaia_id", "g_mag")}
        if s.get("name"):
            marker["name"] = s["name"]
        if _star_key(s) in labelled:
            marker["label"] = True
            marker.setdefault("name", star_identifier(s))  # unnamed: its catalogue ID
        markers.append(marker)
    markers += [{"kind": "detection", "x": d["x"], "y": d["y"]} for d in shown_detections]
    for o in shown_objects:
        marker = {k: o[k] for k in _OBJECT_FIELDS}
        if o["id"] in labelled and o["eligible"]:
            marker["label"] = True
        markers.append(marker)

    object_markers = [m for m in markers if m["kind"] == "object"]
    totals = analysis["totals"]
    counts = {
        "detections": totals["detections"],
        "detections_marked": len(shown_detections),
        "detections_unmatched": len(detections),
        "matched_stars": totals["matched_stars"],
        "stars_marked": len(shown_stars),
        "named_stars": sum(1 for m in markers if m["kind"] == "star" and m.get("label")),
        "objects": len(object_markers),
        "objects_total": totals["objects"],
        "objects_labelled": sum(1 for m in object_markers if m.get("label")),
    }
    return {
        "schema_version": 1,
        "filename": analysis["filename"],
        "image": analysis["image"],
        "outcome": analysis["outcome"],
        "sky": analysis["sky"],
        "counts": counts,
        "style": analysis["style"],
        "legend": _legend(counts, object_markers),
        "markers": markers,
        "objects": [_object_summary(m) for m in object_markers],
        "notes": analysis["notes"],
        "view": {
            "options": options.to_dict(),
            "default": options.is_default,
            "assessed": analysis.get("assessed", False),
            "types": available_types(analysis),
            "requests": requests,
        },
    }


_OBJECT_FIELDS = (
    "kind",
    "id",
    "x",
    "y",
    "centre_in_image",
    "name",
    "common_name",
    "type",
    "category",
    "color",
    "footprint",
    "support",
    "explanation",
    "magnitude",
)


def available_types(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """The marker types this image has, with counts, in display order."""
    pool = analysis["pool"]
    counts: dict[str, int] = {}
    for o in pool["objects"]:
        counts[o["category"]] = counts.get(o["category"], 0) + 1
    order = [c for c in _TYPE_ORDER if c in counts]
    order += sorted(c for c in counts if c not in _TYPE_ORDER)
    types = [
        {
            "key": c,
            "label": TYPE_LABELS.get(c, c.replace("_", " ").capitalize()),
            "count": counts[c],
            "color": hex_color(CATEGORY_COLORS.get(c, DEFAULT_COLOR)),
        }
        for c in order
    ]
    if pool["stars"]:
        types.append(
            {
                "key": CATEGORY_STAR,
                "label": TYPE_LABELS[CATEGORY_STAR],
                "count": len(pool["stars"]),
                "color": hex_color(STAR_COLOR),
            }
        )
    if pool["detections"]:
        types.append(
            {
                "key": TYPE_DETECTION,
                "label": TYPE_LABELS[TYPE_DETECTION],
                "count": len(pool["detections"]),
                "color": hex_color(DETECTION_COLOR),
            }
        )
    return types


def star_identifier(star: dict[str, Any]) -> str:
    """Label for a matched star without a name: its SIMBAD main ID (e.g. ``TYC 2642-385-1``),
    else its Gaia DR3 source ID."""
    main_id = " ".join(str(star.get("main_id") or "").split())
    for prefix in ("* ", "V* "):
        if main_id.startswith(prefix):
            main_id = main_id[len(prefix) :]
    return main_id or f"Gaia DR3 {star['gaia_id']}"


def _star_key(star: dict[str, Any]) -> str:
    return f"gaia:{star['gaia_id']}"


def _star_order(star: dict[str, Any]) -> tuple[bool, float]:
    g = star.get("g_mag")
    return (g is None, g if g is not None else 0.0)


def _brightness_rank(kind: str, index: int, marker: dict[str, Any]) -> tuple:
    """Brightest first; objects without a catalogued magnitude last, in their usual order."""
    mag = marker.get("g_mag") if kind == "star" else (marker.get("magnitude") or {}).get("value")
    if mag is None or not math.isfinite(mag):
        return (1, 0.0, 0 if kind == "object" else 1, index)
    return (0, mag, 0 if kind == "object" else 1, index)


# --------------------------------------------------------------------------- requests


def _search_records(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything a request can name: drawn objects and Gaia-matched stars."""
    records = []
    for o in analysis["pool"]["objects"]:
        records.append(
            {
                "display_name": o["name"],
                "main_id": o.get("main_id") or o["name"],
                "aliases": o.get("aliases") or [],
                "common_names": o.get("common_names") or [],
                "catalogue_id": o["id"],
                "object_type": o.get("type"),
                "_key": o["id"],
                "_marker": o,
            }
        )
    for s in analysis["pool"]["stars"]:
        gaia = f"Gaia DR3 {s['gaia_id']}"
        records.append(
            {
                "display_name": s.get("name") or gaia,
                "main_id": s.get("main_id") or gaia,
                "aliases": [*(s.get("aliases") or []), gaia, s["gaia_id"]],
                "common_names": s.get("common_names") or [],
                "catalogue_id": s["gaia_id"],
                "object_type": "Gaia-matched star",
                "_key": _star_key(s),
                "_marker": s,
            }
        )
    return records


def _resolve_requests(analysis: dict[str, Any], labels: tuple[str, ...]):
    """``(answers, keys)``: one answer per requested name, and the marker keys to show and
    name (``catalogue_id`` for objects, ``"gaia:<id>"`` for stars)."""
    if not labels:
        return [], set()
    records = _search_records(analysis)
    answers, keys = [], set()
    for query in labels:
        answer = {"query": query, "status": REQUEST_NOT_FOUND, "name": None, "message": ""}
        try:
            found = find_record(records, query, "objects and stars in this image")
        except ObjectSelectionError as exc:
            answer.update(status=REQUEST_AMBIGUOUS, message=_ambiguity_text(query, str(exc)))
            answers.append(answer)
            continue
        if found is not None:
            record = found.record
            marker = record["_marker"]
            answer["name"] = record["display_name"]
            keys.add(record["_key"])  # shown either way, so it can be found on the image
            if marker["kind"] == "star" or marker["eligible"]:
                answer.update(status=REQUEST_LABELLED, message="Named on the image.")
            else:
                level = SUPPORT_TEXT.get(marker.get("support") or "", "").lower()
                why = f" ({level})" if level else " (the evidence step didn't grade it)"
                answer.update(
                    status=REQUEST_NOT_DETECTED,
                    message="The catalogue places it here and it is marked, but the image shows "
                    f"no clear sign of it{why}, so it isn't named.",
                )
            answers.append(answer)
            continue
        answer.update(_explain_missing(analysis, query))
        answers.append(answer)
    return answers, keys


def _ambiguity_text(query: str, error: str) -> str:
    detail = error.split(": ", 1)[-1].rsplit(". Use", 1)[0]
    return f"“{query}” matches more than one thing here ({detail}). Use a more specific name."


def _explain_missing(analysis: dict[str, Any], query: str) -> dict[str, Any]:
    """Why ``query`` names nothing that can be labelled: a catalogue row of this field that
    wasn't detected, isn't a marked type, or is outside the image; or nothing at all."""
    rows = analysis.get("lookup") or []
    try:
        found = find_record(rows, query, "catalogue rows")
    except ObjectSelectionError as exc:
        return {"status": REQUEST_AMBIGUOUS, "message": _ambiguity_text(query, str(exc))}
    if found is not None:
        row = found.record
        name = row["display_name"]
        if row.get("status") == "outside_field":
            return {
                "status": REQUEST_OUTSIDE,
                "name": name,
                "message": "It is near this field but outside the image.",
            }
        if row.get("category") == CATEGORY_STAR:
            return {
                "status": REQUEST_NOT_DETECTED,
                "name": name,
                "message": "It is in the image area, but no detected point of light was matched "
                "to it in Gaia DR3 (very bright stars can be saturated or split up), so it "
                "isn't named.",
            }
        if row.get("status") == "in_field_type_excluded":
            kind = row.get("type") or "this kind of object"
            return {
                "status": REQUEST_NOT_ANNOTATED,
                "name": name,
                "message": f"It is catalogued in this field, but as a {kind}, a kind of object "
                "AstroIdentify doesn't mark.",
            }
        return {
            "status": REQUEST_OUTSIDE,
            "name": name,
            "message": "No part of it falls inside the image.",
        }
    gaia_id = _gaia_id(query)
    if gaia_id is not None and gaia_id in set(analysis.get("gaia_in_image") or ()):
        return {
            "status": REQUEST_NOT_DETECTED,
            "name": f"Gaia DR3 {gaia_id}",
            "message": "This Gaia star is in the image area and bright enough to be detected, "
            "but no detected point of light was matched to it, so it isn't named.",
        }
    return {
        "status": REQUEST_NOT_FOUND,
        "message": f"Nothing found in this image's catalogue results matches “{query}”. Names "
        "and catalogue IDs (for example M 57, NGC 6720, HD 183987 or a Gaia DR3 source ID) are "
        "matched exactly, ignoring case and spaces.",
    }


def _gaia_id(query: str) -> str | None:
    text = "".join(query.split()).casefold()
    for prefix in ("gaiadr3", "gaia"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text if text.isdigit() else None


# --------------------------------------------------------------------------- legend


def _legend(counts: dict[str, int], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legend = []
    if counts["objects"]:
        legend.append(
            {
                "kind": "object",
                "label": "Catalogued object",
                "detail": "A galaxy, nebula or cluster the catalogue places here: outlined when "
                "its size is catalogued, a crosshair when it isn't. Named on the image only when "
                "the image shows evidence of it; all are listed beside the image.",
                "colors": sorted({o["color"] for o in objects}),
            }
        )
    if counts["stars_marked"]:
        detail = "A detected point of light matched to a star in Gaia DR3."
        if counts["stars_marked"] < counts["matched_stars"]:
            detail += f" {counts['stars_marked']} of {counts['matched_stars']} are marked."
        if counts["named_stars"]:
            detail += " Named when the star has a catalogued name."
        legend.append({"kind": "star", "label": "Matched star", "detail": detail})
    if counts["detections_marked"]:
        unmatched = counts["detections_unmatched"]
        detail = "A point of light found in the image, not matched to a catalogue."
        if counts["detections_marked"] < unmatched:
            detail += f" The brightest {counts['detections_marked']} of {unmatched} are marked."
        legend.append({"kind": "detection", "label": "Detected point", "detail": detail})
    return legend


def _object_summary(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": o["id"],
        "name": o["name"],
        "common_name": o["common_name"],
        "type": o["type"],
        "category": o["category"],
        "color": o["color"],
        "support": o["support"],
        "support_text": SUPPORT_TEXT.get(o["support"]) if o["support"] else None,
        "explanation": o["explanation"],
        "magnitude": o.get("magnitude"),
        "labelled": bool(o.get("label")),
        "centre_in_image": o["centre_in_image"],
    }

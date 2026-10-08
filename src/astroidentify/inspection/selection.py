"""Post-identification object selection from saved Milestone 5 tables.

The user's name is used **only** to pick a row that Milestone 5 already produced. It never
reaches detection, plate solving, catalogue queries or evidence grading.

Matching is exact after harmless normalization (case-folding and collapsing whitespace),
in priority tiers:

1. display name;
2. SIMBAD main identifier;
3. aliases (all SIMBAD identifiers, plus common names without the ``NAME`` prefix).

The first tier with matches decides. More than one *object* matching within that tier is an
error listing the candidates; no fuzzy matching is attempted.

Objects that Milestone 5 retained are searched first. Only if none matches are rows that
Milestone 5 placed in the image but excluded by its type policy
(``in_field_type_excluded``, e.g. named stars) searched, with the same tiers. An explicit
request may inspect such a row; the selection records that it was excluded from the normal
overlay. Milestone 5's filtering and every other overlay are unchanged. Rows outside the
image cannot be selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astroidentify.exceptions import AstroIdentifyError

TIERS = ("display_name", "main_id", "alias")
STATUS_RETAINED = "catalogued_in_field"
STATUS_TYPE_EXCLUDED = "in_field_type_excluded"


class ObjectSelectionError(AstroIdentifyError):
    """The requested name matches no retained object, or several."""


@dataclass(frozen=True)
class Selection:
    record: dict[str, Any]
    method: str  # one of TIERS
    matched_text: str
    query: str
    lower_tier_matches: tuple[str, ...]  # other objects matching only at a later tier
    excluded_from_overlay: bool = False  # a type-excluded Milestone 5 row, explicitly selected


def normalise(text: str) -> str:
    """Case-fold and collapse whitespace (``"ngc  7000 "`` -> ``"ngc 7000"``)."""
    return " ".join(str(text).split()).casefold()


def _names(record: dict[str, Any], tier: str) -> list[str]:
    if tier == "display_name":
        return [record.get("display_name") or ""]
    if tier == "main_id":
        return [record.get("main_id") or ""]
    aliases = list(record.get("aliases") or ())
    return aliases + list(record.get("common_names") or ())


def _status(record: dict[str, Any]) -> str:
    if record.get("status"):
        return record["status"]
    return STATUS_RETAINED if record.get("retained") else "unknown"


def _tiered(candidates: list[dict[str, Any]], query: str, kind: str) -> Selection | None:
    """Tiered exact match within ``candidates``; ``None`` if nothing matches."""
    wanted = normalise(query)
    hits: dict[str, list[tuple[dict, str]]] = {tier: [] for tier in TIERS}
    for record in candidates:
        for tier in TIERS:
            match = next((n for n in _names(record, tier) if normalise(n) == wanted), None)
            if match is not None:
                hits[tier].append((record, match))
                break  # an object is counted once, at its best tier
    for index, tier in enumerate(TIERS):
        found = hits[tier]
        if not found:
            continue
        if len(found) > 1:
            listed = "; ".join(
                f"{r['display_name']} [{r.get('main_id')}, {r.get('object_type')}, "
                f"id {r['catalogue_id']}]"
                for r, _ in sorted(found, key=lambda item: item[0]["catalogue_id"])
            )
            raise ObjectSelectionError(
                f"{query!r} matches {len(found)} {kind} by {tier}: {listed}. "
                "Use a more specific identifier."
            )
        record, matched = found[0]
        later = tuple(r["display_name"] for t in TIERS[index + 1 :] for r, _ in hits[t])
        return Selection(record, tier, matched, query, later)
    return None


def select_object(objects: list[dict[str, Any]], query: str) -> Selection:
    """Pick the object matching ``query`` (see module docstring).

    Raises:
        ObjectSelectionError: No match in the image, or an ambiguous match.
    """
    if not normalise(query):
        raise ObjectSelectionError("an object name is required")
    retained = [o for o in objects if _status(o) == STATUS_RETAINED]
    selection = _tiered(retained, query, "identified objects")
    if selection is not None:
        return selection
    excluded = [o for o in objects if _status(o) == STATUS_TYPE_EXCLUDED]
    selection = _tiered(excluded, query, "type-excluded catalogue rows in the image")
    if selection is not None:
        return Selection(
            selection.record, selection.method, selection.matched_text, query,
            selection.lower_tier_matches, excluded_from_overlay=True,
        )  # fmt: skip

    wanted = normalise(query)
    elsewhere = [
        o for o in objects if any(normalise(n) == wanted for t in TIERS for n in _names(o, t))
    ]  # fmt: skip
    if elsewhere:
        o = elsewhere[0]
        raise ObjectSelectionError(
            f"{query!r} is in the catalogue table ({o['display_name']}) but its Milestone 5 status "
            f"is {_status(o)!r}: it is not in the image, so it cannot be inspected"
        )
    raise ObjectSelectionError(
        f"no object in the image matches {query!r} (display names, main IDs and aliases are "
        "matched exactly, ignoring case and spacing)"
    )

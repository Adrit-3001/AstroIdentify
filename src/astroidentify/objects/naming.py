"""Display names: a deterministic, catalogue-based preference over an object's identifiers.

SIMBAD lists every identifier of an object (e.g. ``"NGC  1234|IRAS 01234+5678|NAME Foo"``),
padded with repeated spaces. Identifiers are normalised by collapsing whitespace. The
display name is the best-ranked identifier:

1. Messier (``M 31``)
2. NGC (``NGC 224``)
3. IC (``IC 342``)
4. other widely used deep-sky catalogues, in the order of ``OTHER_CATALOGUES``
5. otherwise SIMBAD's ``main_id``

Ties within a rank go to the shorter, then lexicographically smaller identifier. ``NAME``
identifiers (common names) are reported separately as ``common_names``; they are never
the display name, so the label always carries a catalogue designation. Nothing here knows
about any particular object.
"""

from __future__ import annotations

import re

#: Rank of identifiers that are not in a preferred catalogue (the ``main_id`` fallback).
FALLBACK_RANK = 99

_PRIMARY = (
    ("Messier", re.compile(r"^M \d+$")),
    ("NGC", re.compile(r"^NGC \d+[A-Za-z]?$")),
    ("IC", re.compile(r"^IC \d+[A-Za-z]?$")),
)

#: Other common deep-sky catalogues (identifier prefixes as SIMBAD writes them).
OTHER_CATALOGUES: tuple[tuple[str, str], ...] = (
    ("Sharpless", "Sh 2-"),
    ("Collinder", "Cl Collinder "),
    ("Melotte", "Cl Melotte "),
    ("Trumpler", "Cl Trumpler "),
    ("Berkeley", "Cl Berkeley "),
    ("King", "Cl King "),
    ("Stock", "Cl Stock "),
    ("Abell PN", "PN A66 "),
    ("Abell clusters", "ACO "),
    ("Barnard", "Barnard "),
    ("vdB", "vdB "),
    ("LBN", "LBN "),
    ("LDN", "LDN "),
    ("UGC", "UGC "),
    ("MCG", "MCG"),
    ("PGC", "PGC "),
    ("PN G", "PN G"),
)


def normalise_identifier(identifier: str) -> str:
    """Collapse runs of whitespace and strip (``"NGC  6543"`` -> ``"NGC 6543"``)."""
    return " ".join(identifier.split())


def split_identifiers(ids: str | None) -> tuple[str, ...]:
    """SIMBAD's pipe-separated identifier list -> normalised, de-duplicated, ordered tuple."""
    if not ids:
        return ()
    seen: dict[str, None] = {}
    for part in ids.split("|"):
        name = normalise_identifier(part)
        if name:
            seen.setdefault(name, None)
    return tuple(seen)


def identifier_rank(identifier: str) -> int:
    """0 = Messier, 1 = NGC, 2 = IC, 3.. = ``OTHER_CATALOGUES``, ``FALLBACK_RANK`` otherwise."""
    for rank, (_, pattern) in enumerate(_PRIMARY):
        if pattern.match(identifier):
            return rank
    for offset, (_, prefix) in enumerate(OTHER_CATALOGUES):
        if identifier.startswith(prefix) and len(identifier) > len(prefix):
            return len(_PRIMARY) + offset
    return FALLBACK_RANK


def common_names(identifiers: tuple[str, ...]) -> tuple[str, ...]:
    """Common names (SIMBAD ``NAME ...`` identifiers, prefix removed)."""
    return tuple(i[5:] for i in identifiers if i.startswith("NAME ") and len(i) > 5)


def display_name(main_id: str, identifiers: tuple[str, ...]) -> tuple[str, int]:
    """``(display name, rank)`` by the preference in the module docstring."""
    candidates = [(identifier_rank(i), len(i), i) for i in identifiers if not i.startswith("NAME ")]
    candidates = [c for c in candidates if c[0] < FALLBACK_RANK]
    if candidates:
        rank, _, name = min(candidates)
        return name, rank
    return normalise_identifier(main_id), FALLBACK_RANK

from __future__ import annotations

import pytest

from astroidentify.objects.filtering import (
    CATEGORY_GALAXY,
    CATEGORY_NEBULA,
    CATEGORY_OTHER,
    CATEGORY_PLANETARY_NEBULA,
    CATEGORY_STAR,
    CATEGORY_STAR_CLUSTER,
    CATEGORY_STELLAR_GROUP,
    CATEGORY_SUPERNOVA_REMNANT,
    CATEGORY_UNKNOWN,
    categorise,
    exclusion_reason,
    is_candidate_type,
)
from astroidentify.objects.naming import (
    FALLBACK_RANK,
    common_names,
    display_name,
    identifier_rank,
    split_identifiers,
)


def test_identifiers_are_normalised_and_deduplicated() -> None:
    ids = split_identifiers("NGC  1234|IRAS 01234+5678||NGC 1234|NAME  Foo  Cloud")
    assert ids == ("NGC 1234", "IRAS 01234+5678", "NAME Foo Cloud")
    assert split_identifiers(None) == ()


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (("UGC 9999", "IC 4321", "NGC 1234", "M 99"), "M 99"),  # Messier preferred
        (("PGC 5", "IC 4321", "NGC 1234"), "NGC 1234"),  # NGC fallback
        (("2MASX J00000000+0000000", "IC 4321"), "IC 4321"),  # IC fallback
        (("Cl Collinder 1", "UGC 10"), "Cl Collinder 1"),  # other common, in listed order
        (("NGC 77", "NGC 7"), "NGC 7"),  # shorter wins within a rank
    ],
)
def test_display_name_preference(ids, expected) -> None:
    assert display_name("SIMBAD MAIN", ids)[0] == expected


def test_display_name_falls_back_to_main_id_and_never_uses_common_names() -> None:
    name, rank = display_name("[XYZ2001]  42", ("[XYZ2001] 42", "NAME Pretty Thing"))
    assert (name, rank) == ("[XYZ2001] 42", FALLBACK_RANK)
    assert common_names(("[XYZ2001] 42", "NAME Pretty Thing")) == ("Pretty Thing",)


def test_identifier_rank_patterns_are_strict() -> None:
    assert identifier_rank("M 1") == 0
    assert identifier_rank("MCG+01-02-003") > 2  # not Messier
    assert identifier_rank("NGC 6822A") == 1
    assert identifier_rank("ICRF J1234+5678") == FALLBACK_RANK  # not IC
    assert identifier_rank("M") == FALLBACK_RANK


@pytest.mark.parametrize(
    ("otype", "path", "category"),
    [
        ("PN", "* > Ev* > PN", CATEGORY_PLANETARY_NEBULA),  # stellar path, explicit override
        ("PN?", "* > Ev* > PN", CATEGORY_PLANETARY_NEBULA),
        ("G", "G", CATEGORY_GALAXY),
        ("QSO", "G > AGN > QSO", CATEGORY_GALAXY),
        ("OpC", "Cl* > OpC", CATEGORY_STAR_CLUSTER),
        ("GlC", "Cl* > GlC", CATEGORY_STAR_CLUSTER),
        ("HII", "ISM > HII", CATEGORY_NEBULA),
        ("RNe", "ISM > Cld > GNe > RNe", CATEGORY_NEBULA),
        ("SNR", "ISM > SNR", CATEGORY_SUPERNOVA_REMNANT),
        ("MGr", "As* > MGr", CATEGORY_STELLAR_GROUP),
        ("*", "*", CATEGORY_STAR),
        ("EB*", "* > ** > EB*", CATEGORY_STAR),
        ("Rad", "Rad", CATEGORY_OTHER),
        ("Zz?", None, CATEGORY_UNKNOWN),
        (None, None, CATEGORY_UNKNOWN),
    ],
)
def test_categories(otype, path, category) -> None:
    assert categorise(otype, path) == category


def test_default_policy_keeps_non_stellar_and_excludes_stars() -> None:
    from astroidentify.config import DEFAULT_OBJECT_CATEGORIES as included

    for kept in (CATEGORY_PLANETARY_NEBULA, CATEGORY_GALAXY, CATEGORY_STAR_CLUSTER):
        assert exclusion_reason(kept, False, included, True) is None
    assert "star" in exclusion_reason(CATEGORY_STAR, False, included, True)
    assert exclusion_reason(CATEGORY_UNKNOWN, False, included, True) is not None
    assert exclusion_reason(CATEGORY_GALAXY, True, included, False) == "candidate type excluded"
    assert is_candidate_type("G?") and not is_candidate_type("?") and not is_candidate_type("G")

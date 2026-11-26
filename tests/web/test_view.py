"""Display options: marker types, brightest only, particular objects (offline)."""

from __future__ import annotations

import pytest

from astroidentify.preprocessing.loader import load_image
from astroidentify.web.annotations import build_analysis, render_annotated
from astroidentify.web.view import ViewOptions, ViewOptionsError, make_view
from tests.web.fixtures import H, W, all_ok, outputs, png


@pytest.fixture
def analysis(tmp_path):
    result, _ = build_analysis(
        load_image(png(tmp_path)),
        outputs(tmp_path),
        all_ok(),
        filename="x.png",
        solve_total_timeout_seconds=900,
    )
    return result


def view(analysis, types=None, brightest=None, labels=()):
    return make_view(analysis, ViewOptions.from_values(types, brightest, labels))


def labelled(result) -> list[str]:
    return [m["name"] for m in result["markers"] if m.get("label")]


def kinds(result) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in result["markers"]:
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    return counts


def answers(result) -> dict[str, dict]:
    return {a["query"]: a for a in result["view"]["requests"]}


# --------------------------------------------------------------------------- the analysis


def test_analysis_names_matched_stars_by_gaia_id_and_keeps_every_candidate(analysis):
    stars = analysis["pool"]["stars"]
    assert [s["gaia_id"] for s in stars] == ["222", "111", "333"]  # brightest first
    assert stars[0]["name"] == "HD 1" and "Gaia DR3 222" in stars[0]["aliases"]
    assert "name" not in stars[1]
    assert len(analysis["pool"]["detections"]) == 2
    objects = {o["name"]: o for o in analysis["pool"]["objects"]}
    assert set(objects) == {"M 57", "LEDA 1", "NGC 2", "NGC 3"}
    assert objects["M 57"]["magnitude"] == {"band": "V", "value": 8.8}
    assert objects["NGC 2"]["magnitude"] == {"band": "B", "value": 12.0}
    assert objects["M 57"]["eligible"] and not objects["LEDA 1"]["eligible"]
    # Rows kept only to explain requests: not drawn objects nor matched stars' rows.
    assert {r["display_name"] for r in analysis["lookup"]} == {"* alf Lyr", "GAVC 1", "NGC 99"}
    assert analysis["gaia_in_image"] == ["444"]  # eligible and unmatched only


def test_default_view_names_supported_objects_and_named_stars(analysis):
    result = view(analysis)
    assert result["view"]["default"]
    assert set(labelled(result)) == {"HD 1", "M 57", "NGC 3", "NGC 2"}
    assert kinds(result) == {"star": 3, "detection": 2, "object": 4}
    assert [t["key"] for t in result["view"]["types"]] == [
        "galaxy", "planetary_nebula", "star", "detection",
    ]  # fmt: skip
    assert {t["key"]: t["count"] for t in result["view"]["types"]}["galaxy"] == 3


# --------------------------------------------------------------------------- types


def test_types_choose_which_markers_are_shown(analysis):
    result = view(analysis, types=["galaxy"])
    assert kinds(result) == {"object": 3}
    assert {m["category"] for m in result["markers"]} == {"galaxy"}
    assert set(labelled(result)) == {"NGC 2", "NGC 3"}  # LEDA 1: marked, never named
    assert result["counts"]["objects"] == 3 and result["counts"]["objects_total"] == 4
    assert [o["name"] for o in result["objects"]] == ["NGC 3", "NGC 2", "LEDA 1"]

    stars_only = view(analysis, types=["star", "detection"])
    assert kinds(stars_only) == {"star": 3, "detection": 2}
    assert labelled(stars_only) == ["HD 1"]
    assert view(analysis, types=[])["markers"] == []


def test_types_accept_comma_lists_and_reject_unknown_names():
    assert ViewOptions.from_values(["galaxy,nebula", "star"]).types == {"galaxy", "nebula", "star"}
    with pytest.raises(ViewOptionsError, match="comet"):
        ViewOptions.from_values(["galaxy,comet"])


# --------------------------------------------------------------------------- brightest


def test_brightest_ranks_stars_and_objects_by_catalogued_magnitude(analysis):
    # HD 1 (G 7.0), M 57 (V 8.8), star 111 (G 9.5), star 333 (G 11.0), NGC 2 (B 12.0), then
    # NGC 3 (no magnitude). Unnamed stars are detected, so they count, by catalogue ID.
    assert labelled(view(analysis, brightest=1)) == ["HD 1"]
    assert set(labelled(view(analysis, brightest=2))) == {"HD 1", "M 57"}
    assert set(labelled(view(analysis, brightest=3))) == {"HD 1", "M 57", "Gaia DR3 111"}
    assert "NGC 2" in labelled(view(analysis, brightest=5))
    every = view(analysis, brightest=50)
    assert set(labelled(every)) == {
        "HD 1", "M 57", "Gaia DR3 111", "Gaia DR3 333", "NGC 2", "NGC 3",
    }  # fmt: skip  # never LEDA 1: the image doesn't show it
    assert len(every["markers"]) == len(view(analysis)["markers"])  # markers are unchanged


def test_unnamed_stars_are_named_by_their_simbad_main_id_when_they_have_one():
    from astroidentify.web.view import star_identifier

    assert star_identifier({"gaia_id": "1", "main_id": "TYC 2642-385-1"}) == "TYC 2642-385-1"
    assert star_identifier({"gaia_id": "1", "main_id": "V* IV Vul"}) == "IV Vul"
    assert star_identifier({"gaia_id": "7"}) == "Gaia DR3 7"


def test_brightest_combines_with_types(analysis):
    assert labelled(view(analysis, types=["galaxy"], brightest=1)) == ["NGC 2"]
    assert labelled(view(analysis, types=["planetary_nebula", "galaxy"], brightest=1)) == ["M 57"]


@pytest.mark.parametrize("bad", [0, -1, 101, "five", 2.5])
def test_brightest_must_be_a_sensible_count(bad):
    with pytest.raises(ViewOptionsError, match="brightest"):
        ViewOptions.from_values(brightest=bad)


# --------------------------------------------------------------------------- requests


@pytest.mark.parametrize(
    "query", ["M 57", "m57", "M  57", "NGC 6720", "ngc6720", "Ring Nebula", "ring  nebula"]
)
def test_request_by_name_main_id_alias_or_common_name(analysis, query):
    result = view(analysis, types=["star"], brightest=1, labels=[query])
    answer = answers(result)[" ".join(query.split())]  # spacing is tidied when asked
    assert answer["status"] == "labelled" and answer["name"] == "M 57"
    assert set(labelled(result)) == {"HD 1", "M 57"}  # added to the brightest, type hidden


@pytest.mark.parametrize("query", ["111", "Gaia DR3 111", "gaia dr3 111"])
def test_request_names_an_unnamed_matched_star_by_its_gaia_id(analysis, query):
    result = view(analysis, types=[], labels=[query])
    (star,) = result["markers"]
    assert star["kind"] == "star" and star["gaia_id"] == "111"
    assert star["label"] and star["name"] == "Gaia DR3 111"


def test_requested_star_outside_the_marker_cap_is_still_shown(analysis, monkeypatch):
    import astroidentify.web.view as view_module

    monkeypatch.setattr(view_module, "MAX_STAR_MARKERS", 1)
    result = view(analysis, labels=["333"])
    assert [m["gaia_id"] for m in result["markers"] if m["kind"] == "star"] == ["222", "333"]


def test_request_for_an_object_the_image_does_not_show_is_marked_not_named(analysis):
    result = view(analysis, types=[], labels=["LEDA 1"])
    (marker,) = result["markers"]
    assert marker["name"] == "LEDA 1" and not marker.get("label")
    answer = answers(result)["LEDA 1"]
    assert answer["status"] == "not_detected" and "isn't named" in answer["message"]


@pytest.mark.parametrize(
    ("query", "status", "text"),
    [
        ("Vega", "not_detected", "no detected point of light"),
        ("alf Lyr", "not_detected", "no detected point of light"),
        ("Gaia DR3 444", "not_detected", "bright enough"),
        ("GAVC 1", "not_annotated", "HI (21cm) Source"),
        ("NGC 99", "outside", "outside the image"),
        ("UGC 5", "ambiguous", "NGC 2"),
        ("Gaia DR3 555", "not_found", "Nothing found"),
        ("Andromeda", "not_found", "Nothing found"),
    ],
)
def test_requests_that_cannot_be_named_say_why(analysis, query, status, text):
    result = view(analysis, labels=[query])
    answer = answers(result)[query]
    assert answer["status"] == status and text in answer["message"]
    assert labelled(result) == labelled(view(analysis))  # nothing else changes


def test_requests_are_deduplicated_and_limited():
    options = ViewOptions.from_values(labels=["M57", " m57 ", "", "NGC 2"])
    assert options.labels == ("M57", "NGC 2")
    with pytest.raises(ViewOptionsError, match="at most"):
        ViewOptions.from_values(labels=[f"NGC {i}" for i in range(21)])
    with pytest.raises(ViewOptionsError, match="characters"):
        ViewOptions.from_values(labels=["x" * 101])


def test_query_parameters():
    options = ViewOptions.from_query(
        {"types": ["galaxy,star"], "brightest": ["3"], "label": ["M 57", "Vega"], "x": ["1"]}
    )
    assert options.types == {"galaxy", "star"} and options.brightest == 3
    assert options.labels == ("M 57", "Vega")
    assert ViewOptions.from_query({"types": [""]}).types == frozenset()  # present: none shown
    assert ViewOptions.from_query({}).is_default


# --------------------------------------------------------------------------- rendering


def test_png_for_a_view_has_the_image_size_and_its_labels(analysis, tmp_path):
    pixels = load_image(png(tmp_path)).data.astype("uint8")
    plain = render_annotated(pixels, view(analysis, types=[]))
    assert plain.size == (W, H)
    assert np_equal(plain, pixels)  # nothing shown: the image itself
    named = render_annotated(pixels, view(analysis, types=[], labels=["M 57"]))
    assert named.size == (W, H) and not np_equal(named, pixels)


def np_equal(image, pixels) -> bool:
    import numpy as np

    return bool(np.array_equal(np.asarray(image), pixels))

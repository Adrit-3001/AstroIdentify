"""Synthetic pipeline outputs for display-option tests (offline, no WCS needed).

Image 64 x 48. Gaia-matched stars: 222 (G 7.0, SIMBAD "HD 1" by its Gaia DR3 alias),
111 (G 9.5, unnamed), 333 (G 11.0, unnamed). Two more detections are unmatched.

Identified objects (evidence level, catalogued magnitude):

* M 57 / NGC 6720 / Ring Nebula - planetary nebula, strong, V 8.8
* NGC 2  - galaxy, weak, B 12.0
* NGC 3  - galaxy, moderate, no magnitude
* LEDA 1 - galaxy, catalogue-only (in the image per the catalogue; not seen)

"UGC 5" is an alias of both NGC 2 and NGC 3. Other catalogue rows: Vega (a star in the field
that no detection was matched to), an HI source (a type that isn't marked) and NGC 99
(outside the image). Gaia 444 is in the image and bright enough but unmatched; 555 is too
faint to have been matched.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

from astroidentify.web.annotations import StepOutcome

W, H = 64, 48
STEPS = ("detect", "solve", "catalog-match", "identify", "assess")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def png(tmp_path: Path, value: int = 10) -> Path:
    path = tmp_path / "input.png"
    Image.fromarray(np.full((H, W, 3), value, np.uint8)).save(path)
    return path


def all_ok() -> dict[str, StepOutcome]:
    return {s: StepOutcome(True) for s in STEPS}


def _object(cid, name, category, x, y, *, status="catalogued_in_field", **extra):
    row = {
        "catalogue_id": cid,
        "display_name": name,
        "main_id": extra.pop("main_id", name),
        "aliases": extra.pop("aliases", [name]),
        "common_names": extra.pop("common_names", []),
        "object_type": extra.pop("object_type", "G"),
        "object_type_description": extra.pop("description", "Galaxy"),
        "category": category,
        "projected_x": x,
        "projected_y": y,
        "footprint_px": None,
        "magnitudes": extra.pop("magnitudes", {}),
        "n_references": extra.pop("n_references", 1),
        "status": status,
        "retained": status == "catalogued_in_field",
    }
    row.update(extra)
    return row


def outputs(tmp_path: Path) -> dict[str, Path]:
    dirs = {s: tmp_path / "steps" / s for s in STEPS}
    write_json(
        dirs["detect"] / "sources.json",
        {
            "sources": [
                {"source_id": 1, "x": 10.0, "y": 12.0, "flux": 500.0, "accepted": True},
                {"source_id": 2, "x": 30.0, "y": 20.0, "flux": 300.0, "accepted": True},
                {"source_id": 3, "x": 50.0, "y": 40.0, "flux": 100.0, "accepted": True},
                {"source_id": 5, "x": 40.0, "y": 8.0, "flux": 200.0, "accepted": True},
                {"source_id": 6, "x": 20.0, "y": 40.0, "flux": 50.0, "accepted": True},
            ]
        },
    )
    write_json(
        dirs["solve"] / "plate_solution.json",
        {
            "solved": True,
            "status": "solved",
            "centre": {"ra_deg": 283.3865, "dec_deg": 33.027},
            "geometry": {"pixel_scale_arcsec": 0.857},
        },
    )
    dirs["catalog-match"].mkdir(parents=True)
    with (dirs["catalog-match"] / "catalog_matches.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["detection_source_id", "gaia_source_id", "phot_g_mean_mag"])
        writer.writerows([[1, 111, 9.5], [2, 222, 7.0], [5, 333, 11.0]])
    with (dirs["catalog-match"] / "gaia_sources.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_id", "phot_g_mean_mag", "in_image", "eligible"])
        writer.writerows(
            [
                [111, 9.5, "true", "true"],
                [222, 7.0, "true", "true"],
                [333, 11.0, "true", "true"],
                [444, 10.0, "true", "true"],
                [555, 19.0, "true", "false"],
            ]
        )
    write_json(dirs["catalog-match"] / "catalog_match_summary.json", {"summary": {"matches": 3}})
    objects = [
        _object(
            7, "M 57", "planetary_nebula", 32.0, 24.0,
            main_id="M  57", aliases=["M  57", "NGC  6720", "NAME Ring Nebula"],
            common_names=["Ring Nebula"], object_type="PN", description="Planetary Nebula",
            magnitudes={"V": 8.8, "B": 9.7}, n_references=800,
        ),
        _object(8, "LEDA 1", "galaxy", 12.0, 36.0),
        _object(10, "NGC 2", "galaxy", 45.0, 30.0, aliases=["NGC 2", "UGC 5"],
                magnitudes={"B": 12.0}),
        _object(11, "NGC 3", "galaxy", 55.0, 10.0, aliases=["NGC 3", "UGC 5"]),
        _object(9, "HD 1", "star", 30.0, 20.0, status="in_field_type_excluded",
                aliases=["HD 1", "Gaia DR3 222"], object_type="*", description="Star"),
        _object(
            12, "* alf Lyr", "star", 5.0, 5.0, status="in_field_type_excluded",
            aliases=["* alf Lyr", "NAME Vega", "Gaia DR3 999"], common_names=["Vega"],
            object_type="*", description="Star",
        ),
        _object(13, "GAVC 1", "other", 20.0, 20.0, status="in_field_type_excluded",
                object_type="HI", description="HI (21cm) Source"),
        _object(14, "NGC 99", "galaxy", 300.0, 300.0, status="outside_field"),
    ]  # fmt: skip
    write_json(dirs["identify"] / "catalog_objects.json", {"objects": objects})
    write_json(
        dirs["identify"] / "identification_summary.json",
        {"image": {"width": W, "height": H, "pixel_scale_arcsec": 0.857}},
    )
    write_json(
        dirs["assess"] / "object_evidence.json",
        {
            "objects": [
                {"catalogue_id": 7, "support_level": "strong", "explanation": "clear"},
                {"catalogue_id": 8, "support_level": "catalogue-only", "explanation": "faint"},
                {"catalogue_id": 10, "support_level": "weak", "explanation": "some"},
                {"catalogue_id": 11, "support_level": "moderate", "explanation": "ok"},
            ]
        },
    )
    return dirs

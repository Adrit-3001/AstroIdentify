"""``astroidentify annotate``: display options on the command line (offline)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from astroidentify.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from tests.web.fixtures import outputs, png, write_json


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _args(tmp_path, dirs, *extra):
    return [
        "annotate", str(png(tmp_path)),
        "--objects", str(dirs["identify"]), "--evidence", str(dirs["assess"]),
        "--detections", str(dirs["detect"]), "--catalog", str(dirs["catalog-match"]),
        "--astrometry", str(dirs["solve"]), "-o", str(tmp_path / "annotated"), *extra,
    ]  # fmt: skip


def test_annotate_writes_the_image_and_answers_each_request(tmp_path, capsys):
    dirs = outputs(tmp_path)
    before = _digest(tmp_path / "steps")
    code = main(
        _args(tmp_path, dirs, "--types", "galaxy", "--brightest", "1", "--label", "M57",
              "--label", "Vega")
    )  # fmt: skip
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "Named (2): M 57, NGC 2" in out or "Named (2): NGC 2, M 57" in out
    assert "Vega (* alf Lyr): It is in the image area" in out
    summary = json.loads((tmp_path / "annotated" / "annotation.json").read_text())
    assert summary["view"]["options"] == {"types": ["galaxy"], "brightest": 1,
                                          "labels": ["M57", "Vega"]}  # fmt: skip
    assert (tmp_path / "annotated" / "annotated.png").is_file()
    assert _digest(tmp_path / "steps") == before  # upstream products untouched


def test_annotate_without_evidence_names_no_object(tmp_path, capsys):
    dirs = outputs(tmp_path)
    args = _args(tmp_path, dirs)
    del args[args.index("--evidence") : args.index("--evidence") + 2]
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "Named (1): HD 1" in out and "Evidence: not available" in out


def test_annotate_inputs_default_to_those_the_identification_recorded(tmp_path, capsys):
    dirs = outputs(tmp_path)
    write_json(
        dirs["identify"] / "identification_summary.json",
        {
            "image": {"width": 64, "height": 48, "pixel_scale_arcsec": 0.857},
            "inputs": {
                "detections": str(dirs["detect"] / "sources.json"),
                "plate_solution": str(dirs["solve"] / "plate_solution.json"),
                "catalog_summary": str(dirs["catalog-match"] / "catalog_match_summary.json"),
            },
        },
    )
    args = ["annotate", str(png(tmp_path)), "--objects", str(dirs["identify"]),
            "--evidence", str(dirs["assess"]), "-o", str(tmp_path / "a")]  # fmt: skip
    assert main(args) == EXIT_OK
    assert "3 stars, 2 other points of light" in capsys.readouterr().out


def test_annotate_usage_and_input_errors(tmp_path, capsys):
    dirs = outputs(tmp_path)
    assert main(_args(tmp_path, dirs, "--types", "comet")) == EXIT_USAGE
    assert "unknown type(s) comet" in capsys.readouterr().err
    assert main(_args(tmp_path, dirs, "--brightest", "0")) == EXIT_USAGE
    args = _args(tmp_path, dirs)
    args[args.index("--objects") + 1] = str(tmp_path)
    assert main(args) == EXIT_ERROR
    assert "not an 'identify' output directory" in capsys.readouterr().err

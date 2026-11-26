"""``astroidentify run CONFIG.toml``: one run file drives the individual commands (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from astroidentify.cli import EXIT_OK, EXIT_USAGE, main
from astroidentify.exceptions import ConfigurationError
from astroidentify.runfile import build_commands, load_run_config, run


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "run.toml"
    path.write_text(text)
    return path


def _commands(tmp_path, text):
    return build_commands(load_run_config(_write(tmp_path, text)))


def test_default_steps_are_wired_in_pipeline_order(tmp_path) -> None:
    out = tmp_path / "out"
    commands = _commands(tmp_path, f'image = "img.png"\nname = "f"\noutput_root = "{out}"\n')
    assert [c[0] for c in commands] == ["detect", "solve", "catalog-match", "identify", "assess"]
    det, astro, cat, objects = (out / f"f-{s}" for s in ("detection", "astrometry", "catalog",
                                                         "objects"))  # fmt: skip
    catalog_match = commands[2]
    assert catalog_match[catalog_match.index("--plate-solution") + 1] == str(
        astro / "plate_solution.json"
    )
    assert catalog_match[catalog_match.index("--detections") + 1] == str(det / "sources.json")
    identify = commands[3]
    assert identify[identify.index("--catalog") + 1] == str(cat)
    assert commands[4][commands[4].index("--objects") + 1] == str(objects)
    assert all(c[1] == "img.png" for c in commands)


def test_step_options_flags_and_overrides(tmp_path) -> None:
    text = """
image = "img.png"
name = "f"
steps = ["assess", "detect"]
verbose = 1

[detect]
detection_sigma = 4.5
"min-snr" = 7
saturation_level = false

[assess]
objects = "elsewhere/objects"
output = "my/evidence"
"""
    detect, assess = _commands(tmp_path, text)
    assert detect[:2] == ["detect", "img.png"]  # pipeline order, whatever the listed order
    assert detect[detect.index("--detection-sigma") + 1] == "4.5"
    assert detect[detect.index("--min-snr") + 1] == "7"
    assert "--saturation-level" not in detect and detect[-1] == "-v"
    assert assess.count("--objects") == 1  # the explicit path replaces the automatic one
    assert assess[assess.index("--objects") + 1] == "elsewhere/objects"
    assert assess[assess.index("--output") + 1] == "my/evidence"


def test_flags_and_multiple_inspections(tmp_path) -> None:
    text = """
image = "img.png"
name = "f"
steps = ["identify", "inspect-object"]

[identify]
no_cache = true

[inspect-object]
object = ["NGC 1", "Some Star"]
label_reference_stars = true
"""
    identify, first, second = _commands(tmp_path, text)
    assert "--no-cache" in identify
    assert first[first.index("--object") + 1] == "NGC 1"
    assert second[second.index("--object") + 1] == "Some Star"
    assert first[first.index("--output") + 1].endswith("f-inspect-ngc-1")
    assert second[second.index("--output") + 1].endswith("f-inspect-some-star")
    assert "--label-reference-stars" in first


def test_later_steps_find_earlier_outputs_on_disk(tmp_path) -> None:
    (tmp_path / "out" / "f-objects").mkdir(parents=True)
    text = f'image = "i.png"\nname = "f"\noutput_root = "{tmp_path / "out"}"\nsteps = ["assess"]\n'
    (assess,) = _commands(tmp_path, text)
    assert assess[assess.index("--objects") + 1] == str(tmp_path / "out" / "f-objects")
    (identify,) = _commands(tmp_path, text.replace('["assess"]', '["identify"]'))
    assert "--catalog" not in identify  # no catalogue run planned or on disk


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('name = "x"\n', "'image'"),
        ('image = "i.png"\nsteps = ["fly"]\n', "unknown steps"),
        ('image = "i.png"\ncolour = 1\n', "unknown keys"),
        ('image = "i.png"\n[detect\n', "invalid TOML"),
        ('image = "i.png"\ndetect = 3\n', "must be a table"),
    ],
)
def test_invalid_run_files(tmp_path, text, message) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_run_config(_write(tmp_path, text))


def test_inspect_needs_an_object(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="object"):
        _commands(tmp_path, 'image = "i.png"\nsteps = ["inspect-object"]\n')


def test_run_stops_at_the_first_failure_and_dry_run_executes_nothing(tmp_path) -> None:
    path = _write(tmp_path, 'image = "i.png"\nname = "f"\n')
    calls, echoed = [], []

    def fake(args):
        calls.append(args[0])
        return 1 if args[0] == "solve" else 0

    assert run(path, execute=fake, echo=echoed.append) == 1
    assert calls == ["detect", "solve"] and "failed" in echoed[-1]
    calls.clear()
    assert run(path, dry_run=True, execute=fake, echo=echoed.append) == 0 and calls == []


def test_unknown_step_option_is_reported_by_the_command_itself(tmp_path, capsys) -> None:
    path = _write(tmp_path, 'image = "i.png"\nsteps = ["detect"]\n[detect]\nsparkle = 3\n')
    assert main(["run", str(path)]) == EXIT_USAGE
    assert "--sparkle" in capsys.readouterr().err


def test_cli_dry_run_and_bad_file(tmp_path, capsys) -> None:
    path = _write(tmp_path, 'image = "i.png"\nname = "f"\n')
    assert main(["run", str(path), "--dry-run"]) == EXIT_OK
    assert "[5/5] astroidentify assess i.png" in capsys.readouterr().out
    assert main(["run", str(tmp_path / "missing.toml")]) == EXIT_USAGE


def test_example_run_file_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "astroidentify.example.toml"
    commands = build_commands(load_run_config(example))
    assert [c[0] for c in commands][-1] == "inspect-object"


def test_annotate_step_is_wired_and_lists_repeat_the_option(tmp_path) -> None:
    out = tmp_path / "out"
    text = f"""
image = "img.png"
name = "f"
output_root = "{out}"

[annotate]
types = ["galaxy", "star"]
brightest = 5
label = ["M 57", "Albireo"]
"""
    commands = _commands(tmp_path, text)
    assert [c[0] for c in commands][-1] == "annotate"  # added when the section is present
    annotate = commands[-1]
    assert annotate[annotate.index("--objects") + 1] == str(out / "f-objects")
    assert annotate[annotate.index("--evidence") + 1] == str(out / "f-evidence")
    assert annotate[annotate.index("--output") + 1] == str(out / "f-annotated")
    assert [annotate[i + 1] for i, a in enumerate(annotate) if a == "--label"] == [
        "M 57",
        "Albireo",
    ]
    assert [annotate[i + 1] for i, a in enumerate(annotate) if a == "--types"] == [
        "galaxy",
        "star",
    ]
    with pytest.raises(ConfigurationError, match="nested"):
        _commands(tmp_path, 'image = "i.png"\n[annotate]\nlabel = [["a"]]\n')

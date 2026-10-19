"""Run several pipeline steps from one TOML file (``astroidentify run CONFIG.toml``).

The file names the image, an output name and the steps to run. It can also have a section
per step whose keys are that command's normal CLI options (``detection_sigma = 4`` or
``"detection-sigma" = 4`` means ``--detection-sigma 4``; ``true`` means a bare flag such as
``--no-cache``; ``false`` omits it). Each step is executed through the ordinary CLI, so
every option, default and validation is exactly that of the individual command.

Outputs go to ``<output_root>/<name>-<suffix>`` and each step is given the previous steps'
outputs automatically, as when the commands are run by hand. A path given explicitly in a
step section always wins. Steps whose inputs already exist from an earlier run can be run
on their own (e.g. ``steps = ["inspect-object"]``).
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from astroidentify.exceptions import ConfigurationError

#: Steps in pipeline order with their output-directory suffixes.
STEPS: dict[str, str] = {
    "preprocess": "preprocess",
    "detect": "detection",
    "solve": "astrometry",
    "catalog-match": "catalog",
    "identify": "objects",
    "assess": "evidence",
    "inspect-object": "inspect",
}
DEFAULT_STEPS = ("detect", "solve", "catalog-match", "identify", "assess")
TOP_LEVEL_KEYS = {"image", "name", "output_root", "steps", "verbose", *STEPS}


def load_run_config(path: str | Path) -> dict[str, Any]:
    """Read and check the run file.

    Raises:
        ConfigurationError: Unreadable/invalid TOML, unknown keys or steps, missing image.
    """
    path = Path(path)
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"could not read run file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc
    unknown = sorted(set(config) - TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigurationError(
            f"unknown keys in {path}: {unknown}; allowed: {sorted(TOP_LEVEL_KEYS)}"
        )
    if not isinstance(config.get("image"), str) or not config["image"]:
        raise ConfigurationError(f"{path}: 'image' (path of the image to process) is required")
    for step in STEPS:
        if step in config and not isinstance(config[step], dict):
            raise ConfigurationError(f"{path}: [{step}] must be a table of CLI options")
    steps = config.get("steps")
    if steps is None:
        steps = list(DEFAULT_STEPS)
        if (config.get("inspect-object") or {}).get("object"):
            steps.append("inspect-object")
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ConfigurationError(f"{path}: 'steps' must be a list of step names")
    bad = [s for s in steps if s not in STEPS]
    if bad:
        raise ConfigurationError(f"{path}: unknown steps {bad}; choose from {list(STEPS)}")
    config["steps"] = [s for s in STEPS if s in steps]  # always run in pipeline order
    config.setdefault("name", Path(config["image"]).stem)
    config.setdefault("output_root", "outputs")
    return config


def output_dir(config: dict[str, Any], step: str, extra: str | None = None) -> Path:
    name = f"{config['name']}-{STEPS[step]}" + (f"-{extra}" if extra else "")
    return Path(config["output_root"]) / name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "object"


def _options(section: dict[str, Any]) -> tuple[list[str], set[str]]:
    """CLI arguments for a step section, and the option names it sets."""
    args, names = [], set()
    for key, value in section.items():
        option = "--" + key.replace("_", "-")
        names.add(option)
        if value is True:
            args.append(option)
        elif value is False or value is None:
            continue
        elif isinstance(value, list | dict):
            raise ConfigurationError(f"option {key!r}: lists/tables are not supported")
        else:
            args += [option, str(value)]
    return args, names


def build_commands(config: dict[str, Any]) -> list[list[str]]:
    """The CLI argument lists for every step of the run, in order."""
    image = config["image"]
    planned = set(config["steps"])

    def available(step: str) -> Path | None:
        """Output of ``step`` if it runs in this run or already exists on disk."""
        directory = output_dir(config, step)
        return directory if step in planned or directory.is_dir() else None

    detections = available("detect")
    astrometry = available("solve")
    catalog = available("catalog-match")
    objects = available("identify")
    verbose = ["-v"] * int(config.get("verbose") or 0)

    commands = []
    for step in config["steps"]:
        section = dict(config.get(step) or {})
        wired: list[tuple[str, Any]] = []
        targets = [None]
        if step == "catalog-match":
            wired = [("--plate-solution", astrometry and astrometry / "plate_solution.json"),
                     ("--detections", detections and detections / "sources.json")]  # fmt: skip
        elif step == "identify":
            wired = [("--astrometry", astrometry), ("--catalog", catalog),
                     ("--detections", detections and detections / "sources.json")]  # fmt: skip
        elif step == "assess":
            wired = [("--objects", objects)]
        elif step == "inspect-object":
            requested = section.pop("object", None)
            targets = requested if isinstance(requested, list) else [requested]
            if not targets or not all(isinstance(t, str) and t for t in targets):
                raise ConfigurationError(
                    '[inspect-object] needs object = "NAME" (or a list of names)'
                )
            wired = [("--objects", objects), ("--catalog", catalog)]
        user_args, user_names = _options(section)
        for target in targets:
            args = [step, image]
            if target is not None:
                args += ["--object", target]
            for option, value in wired:
                if value is not None and option not in user_names:
                    args += [option, str(value)]
            if "--output" not in user_names:
                out = output_dir(config, step, _slug(target) if target is not None else None)
                args += ["--output", str(out)]
            commands.append(args + user_args + verbose)
    return commands


def run(
    config_path: str | Path,
    *,
    dry_run: bool = False,
    execute: Callable[[Sequence[str]], int] | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """Run (or with ``dry_run`` only print) every step; stop at the first failure.

    Returns the exit code of the first failing step, or 0.
    """
    config = load_run_config(config_path)
    commands = build_commands(config)
    if execute is None:
        from astroidentify.cli import main as execute
    for index, args in enumerate(commands, start=1):
        echo(f"[{index}/{len(commands)}] astroidentify {shlex.join(args)}")
        if dry_run:
            continue
        try:
            code = execute(args)
        except SystemExit as exc:  # argparse rejects an option from the run file
            code = exc.code if isinstance(exc.code, int) else 2
        if code != 0:
            echo(f"step {args[0]!r} failed with exit code {code}; stopping")
            return code
    return 0

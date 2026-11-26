"""Analyse one uploaded image (``python -m astroidentify.web.worker JOB_DIR``).

The analysis is the ordinary pipeline: the same step commands as ``astroidentify run``
(``runfile.build_commands``), executed through the normal CLI entry point, writing the
normal outputs into ``JOB_DIR/steps``. Before each step the job's progress is updated so the
browser can show the stage. A step that fails stops the pipeline, and whatever succeeded
before it is still annotated (for example: detections only, when the plate solve fails).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
import traceback
from pathlib import Path

from astroidentify.cli import main as cli_main
from astroidentify.exceptions import AstroIdentifyError
from astroidentify.preprocessing.loader import load_image
from astroidentify.runfile import DEFAULT_STEPS, build_commands, output_dir
from astroidentify.web.annotations import StepOutcome, build_analysis, render_annotated
from astroidentify.web.jobs import DONE, FAILED, read_json, write_json_atomic
from astroidentify.web.view import make_view

RUN_NAME = "result"


class Progress:
    """Writes the job's progress into ``job.json`` (the server only reads it meanwhile)."""

    def __init__(self, job_dir: Path) -> None:
        self.path = job_dir / "job.json"
        self.completed: list[str] = []
        self.current: str | None = None
        self.failed: str | None = None

    def _write(self, **fields) -> None:
        job = read_json(self.path) or {}
        job.update(fields)
        write_json_atomic(self.path, job)

    def stage(self, key: str) -> None:
        if self.current is not None:
            self.completed.append(self.current)
        self.current = key
        self._write(progress={"stage": key, "completed": list(self.completed), "failed": None})

    def fail_stage(self) -> None:
        self.failed, self.current = self.current, None
        self._write(
            progress={"stage": None, "completed": list(self.completed), "failed": self.failed}
        )

    def done(self, result_summary: dict) -> None:
        if self.current is not None:
            self.completed.append(self.current)
            self.current = None
        self._write(
            state=DONE,
            finished_ts=time.time(),
            progress={"stage": None, "completed": list(self.completed), "failed": self.failed},
            result=result_summary,
        )

    def fail(self, message: str) -> None:
        self._write(state=FAILED, finished_ts=time.time(), error=message)


def _run_step(args: list[str]) -> tuple[int, str | None]:
    """Run one CLI step in-process; return its exit code and its error message (if any)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(args)
        except SystemExit as exc:  # argparse rejected an option
            code = exc.code if isinstance(exc.code, int) else 2
    printed = err.getvalue()
    sys.stdout.write(f"$ astroidentify {' '.join(args)}\n{out.getvalue()}{printed}\n")
    sys.stdout.flush()
    errors = [
        line.split("error:", 1)[1].strip()
        for line in printed.splitlines()
        if line.startswith("astroidentify: error:")
    ]
    return code, (errors[-1] if errors else None)


def analyze(job_dir: Path, solve_timeout: float, solve_total_timeout: float, cache_dir: str) -> int:
    job_dir = Path(job_dir).resolve()
    job = read_json(job_dir / "job.json") or {}
    progress = Progress(job_dir)
    progress.stage("load")
    uploads = sorted((job_dir / "upload").glob("input*"))
    if not uploads:
        progress.fail("The uploaded file is missing.")
        return 1
    try:
        image = load_image(uploads[0])
    except AstroIdentifyError as exc:
        progress.fail_stage()
        progress.fail(f"This file couldn't be read as an image: {exc}")
        return 0

    config = {
        "image": str(uploads[0]),
        "name": RUN_NAME,
        "output_root": str(job_dir / "steps"),
        "steps": list(DEFAULT_STEPS),
        "solve": {"timeout": solve_timeout, "total_timeout": solve_total_timeout},
        "catalog-match": {"cache_dir": cache_dir},
        "identify": {"cache_dir": cache_dir},
    }
    outcomes: dict[str, StepOutcome] = {}
    for args in build_commands(config):
        step = args[0]
        progress.stage(step)
        code, error = _run_step(args)
        outcomes[step] = StepOutcome(ok=code == 0, error=error)
        if code != 0:
            progress.fail_stage()
            break

    progress.stage("annotate")
    step_dirs = {step: output_dir(config, step) for step in DEFAULT_STEPS}
    analysis, pixels = build_analysis(
        image,
        step_dirs,
        outcomes,
        filename=job.get("filename") or uploads[0].name,
        solve_total_timeout_seconds=solve_total_timeout,
    )
    result = make_view(analysis)  # the default view; others are made on request
    result_dir = job_dir / "result"
    result_dir.mkdir(exist_ok=True)
    from PIL import Image

    Image.fromarray(pixels).save(result_dir / "original.png", optimize=False)
    render_annotated(pixels, result).save(result_dir / "annotated.png", optimize=False)
    write_json_atomic(result_dir / "analysis.json", analysis)
    write_json_atomic(result_dir / "result.json", result)
    progress.done({"outcome": result["outcome"]["kind"], "counts": result["counts"]})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse one uploaded image (web job).")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--solve-timeout", type=float, default=300.0)
    parser.add_argument("--solve-total-timeout", type=float, default=900.0)
    parser.add_argument("--cache-dir", default="outputs/.catalog-cache")
    args = parser.parse_args(argv)
    try:
        return analyze(args.job_dir, args.solve_timeout, args.solve_total_timeout, args.cache_dir)
    except Exception:
        traceback.print_exc()
        Progress(Path(args.job_dir)).fail(
            "Something went wrong while analysing this image. Details are in the job's worker.log."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())

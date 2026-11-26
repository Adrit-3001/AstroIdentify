"""Jobs: one directory per upload, state on disk, a single-worker queue, and retention.

Directory layout (``<data_dir>/<job id>/``)::

    job.json         state, progress and (when done) the result summary
    upload/input.EXT the uploaded file (the visitor's file name is never used as a path)
    steps/           the ordinary pipeline outputs (result-detection/, result-astrometry/, ...)
    result/          result.json, original.png, annotated.png
    worker.log       everything the pipeline printed

Retention: a finished job is deleted ``retention_hours`` after it finished (the upload, the
pipeline outputs and the images). Jobs left queued or running by a stopped server are marked
failed at start-up, and then expire the same way.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from astroidentify.web.settings import WebSettings
from astroidentify.web.validation import UploadRejected, check_content

JOB_ID = re.compile(r"^[0-9a-f]{32}$")

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
FINAL_STATES = (DONE, FAILED)

#: Pipeline stages as the visitor sees them (keys are the CLI step names where there is one).
STAGES: tuple[tuple[str, str], ...] = (
    ("load", "Reading the image"),
    ("detect", "Finding points of light"),
    ("solve", "Working out where the image points"),
    ("catalog-match", "Matching stars with Gaia DR3"),
    ("identify", "Looking up catalogued objects"),
    ("assess", "Checking the evidence"),
    ("annotate", "Drawing the annotated image"),
)

_CHUNK = 1 << 20


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON so a concurrent reader sees either the old or the new file, never half."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class JobStore:
    """Job directories under one root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def directory(self, job_id: str) -> Path | None:
        """The job's directory, or ``None`` for a malformed or unknown id."""
        if not JOB_ID.fullmatch(job_id or ""):
            return None
        path = self.root / job_id
        return path if (path / "job.json").is_file() else None

    def read(self, job_id: str) -> dict[str, Any] | None:
        directory = self.directory(job_id)
        return read_json(directory / "job.json") if directory else None

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        directory = self.directory(job_id)
        if directory is None:
            raise KeyError(job_id)
        with self._lock:
            job = read_json(directory / "job.json") or {}
            job.update(fields)
            write_json_atomic(directory / "job.json", job)
        return job

    def create(self, display_name: str, ext: str, body: BinaryIO, length: int) -> dict[str, Any]:
        """Store an upload of exactly ``length`` bytes from ``body`` as a new queued job.

        Raises:
            UploadRejected: the body is shorter than announced or its content is not an
                image of the claimed type (the job directory is removed).
        """
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        (directory / "upload").mkdir(parents=True)
        target = directory / "upload" / f"input{ext}"
        try:
            remaining = length
            with target.open("wb") as fh:
                while remaining > 0:
                    chunk = body.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise UploadRejected("The upload was interrupted. Please try again.")
                    fh.write(chunk)
                    remaining -= len(chunk)
            check_content(target, ext)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        job = {
            "id": job_id,
            "filename": display_name,
            "extension": ext,
            "size_bytes": length,
            "state": QUEUED,
            "created_at": utc_now(),
            "created_ts": time.time(),
            "started_ts": None,
            "finished_ts": None,
            "progress": {"stage": None, "completed": [], "failed": None},
            "error": None,
            "result": None,
        }
        write_json_atomic(directory / "job.json", job)
        return job

    def finish_failed(self, job_id: str, message: str) -> None:
        self.update(job_id, state=FAILED, error=message, finished_ts=time.time())

    def mark_interrupted(self) -> list[str]:
        """Mark jobs a previous server left queued/running as failed; return their ids."""
        marked = []
        for path in self.root.glob("*/job.json"):
            job = read_json(path)
            if job and job.get("state") not in FINAL_STATES:
                self.finish_failed(job["id"], "The server restarted before this analysis finished.")
                marked.append(job["id"])
        return marked

    def cleanup(self, retention_seconds: float, now: float | None = None) -> list[str]:
        """Delete finished jobs older than the retention period (and stray directories)."""
        now = time.time() if now is None else now
        removed = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not JOB_ID.fullmatch(directory.name):
                continue
            job = read_json(directory / "job.json")
            if job is None:  # an upload that never completed
                stamp = directory.stat().st_mtime
            elif job.get("state") in FINAL_STATES:
                stamp = job.get("finished_ts") or job.get("created_ts") or 0
            else:
                continue  # queued or running: never removed underneath the worker
            if now - stamp >= retention_seconds:
                shutil.rmtree(directory, ignore_errors=True)
                removed.append(directory.name)
        return removed


def worker_command(job_dir: Path, settings: WebSettings) -> list[str]:
    """The command that analyses one job in its own process."""
    return [
        sys.executable,
        "-m",
        "astroidentify.web.worker",
        str(job_dir),
        "--solve-timeout",
        str(settings.solve_attempt_timeout_seconds),
        "--solve-total-timeout",
        str(settings.solve_total_timeout_seconds),
        "--cache-dir",
        str(Path(settings.catalog_cache_dir).resolve()),
    ]


class JobRunner:
    """Runs queued jobs one at a time, each in a separate worker process.

    One at a time because plate solving uses every CPU core; separate processes so a slow or
    crashing analysis never blocks the web server, and can be stopped at the time limit.
    """

    def __init__(
        self,
        store: JobStore,
        settings: WebSettings,
        command: Callable[[Path, WebSettings], list[str]] = worker_command,
    ) -> None:
        self.store = store
        self.settings = settings
        self._command = command
        self._waiting: deque[str] = deque()
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._loop, name="astroidentify-jobs", daemon=True)
        self._thread.start()

    def submit(self, job_id: str) -> int:
        """Queue a job; return its 1-based place in the queue.

        Raises:
            UploadRejected: the queue is full (HTTP 503).
        """
        with self._cond:
            if len(self._waiting) >= self.settings.max_queued_jobs:
                raise UploadRejected(
                    "Too many images are waiting to be analysed. Please try again later.",
                    status=503,
                )
            self._waiting.append(job_id)
            self._cond.notify()
            return len(self._waiting)

    def position(self, job_id: str) -> int | None:
        with self._cond:
            try:
                return self._waiting.index(job_id) + 1
            except ValueError:
                return None

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._waiting:
                    self._cond.wait()
                job_id = self._waiting[0]
            try:
                self._run(job_id)
            except Exception as exc:  # keep the queue alive whatever happens
                if self.store.directory(job_id):
                    self.store.finish_failed(job_id, f"The analysis could not be run ({exc}).")
            finally:
                with self._cond:
                    self._waiting.popleft()

    def _run(self, job_id: str) -> None:
        directory = self.store.directory(job_id)
        if directory is None:  # removed while waiting
            return
        self.store.update(job_id, state=RUNNING, started_ts=time.time())
        limit = self.settings.job_timeout_seconds
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen(
                self._command(directory, self.settings),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # its own process group: the solver dies with it
            )
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                _kill_group(process)
                self.store.finish_failed(
                    job_id,
                    f"The analysis took longer than {limit / 60:.0f} minutes and was stopped.",
                )
                return
        job = self.store.read(job_id) or {}
        if job.get("state") not in FINAL_STATES:
            self.store.finish_failed(
                job_id, f"The analysis stopped unexpectedly (worker exit code {code})."
            )


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait()


def start_cleanup(store: JobStore, settings: WebSettings) -> threading.Thread:
    """Remove expired jobs now and then every ``cleanup_interval_seconds``."""

    def loop() -> None:
        while True:
            store.cleanup(settings.retention_seconds)
            time.sleep(settings.cleanup_interval_seconds)

    thread = threading.Thread(target=loop, name="astroidentify-cleanup", daemon=True)
    thread.start()
    return thread

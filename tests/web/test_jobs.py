"""Job directories, retention and the single-worker runner (with fake worker commands)."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

from astroidentify.web.jobs import DONE, FAILED, QUEUED, RUNNING, JobRunner, JobStore
from astroidentify.web.settings import WebSettings
from astroidentify.web.validation import UploadRejected

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _create(store: JobStore, data: bytes = PNG, name: str = "m57.png") -> dict:
    return store.create(name, ".png", io.BytesIO(data), len(data))


def test_create_stores_the_upload_under_a_fixed_name(tmp_path):
    store = JobStore(tmp_path)
    job = _create(store, name="../../evil.png")
    directory = tmp_path / job["id"]
    assert (directory / "upload" / "input.png").read_bytes() == PNG
    assert job["state"] == QUEUED and job["filename"] == "../../evil.png"
    assert not (tmp_path.parent / "evil.png").exists()
    assert store.read(job["id"])["id"] == job["id"]


def test_create_rejects_a_short_body_and_leaves_nothing(tmp_path):
    store = JobStore(tmp_path)
    with pytest.raises(UploadRejected):
        store.create("m57.png", ".png", io.BytesIO(PNG[:10]), len(PNG))
    assert list(tmp_path.iterdir()) == []


def test_create_rejects_wrong_content_and_leaves_nothing(tmp_path):
    store = JobStore(tmp_path)
    with pytest.raises(UploadRejected):
        _create(store, data=b"not a png at all, just text")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("job_id", ["", "../etc", "ABCDEF", "0" * 31, "g" * 32])
def test_directory_rejects_malformed_ids(tmp_path, job_id):
    assert JobStore(tmp_path).directory(job_id) is None


def test_cleanup_removes_only_expired_finished_jobs(tmp_path):
    store = JobStore(tmp_path)
    old, recent, running = (_create(store) for _ in range(3))
    now = time.time()
    store.update(old["id"], state=DONE, finished_ts=now - 7200)
    store.update(recent["id"], state=FAILED, finished_ts=now - 60)
    store.update(running["id"], state=RUNNING)
    stray = tmp_path / ("f" * 32)
    stray.mkdir()  # an upload that never got a job.json
    import os

    os.utime(stray, (now - 7200, now - 7200))
    removed = store.cleanup(retention_seconds=3600, now=now)
    assert sorted(removed) == sorted([old["id"], "f" * 32])
    assert store.directory(recent["id"]) and store.directory(running["id"])


def test_mark_interrupted_fails_jobs_left_by_a_previous_server(tmp_path):
    store = JobStore(tmp_path)
    queued, running, done = (_create(store) for _ in range(3))
    store.update(running["id"], state=RUNNING)
    store.update(done["id"], state=DONE, finished_ts=time.time())
    assert sorted(store.mark_interrupted()) == sorted([queued["id"], running["id"]])
    assert store.read(queued["id"])["state"] == FAILED
    assert "restarted" in store.read(running["id"])["error"]
    assert store.read(done["id"])["state"] == DONE


def _fake_worker(script: str):
    def command(job_dir: Path, settings: WebSettings) -> list[str]:
        return [sys.executable, "-c", script, str(job_dir)]

    return command


FINISH = """
import json, sys, time, pathlib
p = pathlib.Path(sys.argv[1]) / "job.json"
job = json.loads(p.read_text()); job.update(state="done", finished_ts=time.time())
p.write_text(json.dumps(job))
"""


def _wait(store, job_id, states=(DONE, FAILED), timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.read(job_id)
        if job and job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stayed {store.read(job_id)['state']}")


def test_runner_runs_jobs_in_a_separate_process(tmp_path):
    store = JobStore(tmp_path)
    runner = JobRunner(store, WebSettings(data_dir=tmp_path), command=_fake_worker(FINISH))
    job = _create(store)
    assert runner.submit(job["id"]) == 1
    finished = _wait(store, job["id"])
    assert finished["state"] == DONE and finished["started_ts"]


def test_runner_reports_a_crashed_worker(tmp_path):
    store = JobStore(tmp_path)
    runner = JobRunner(
        store, WebSettings(data_dir=tmp_path), command=_fake_worker("raise SystemExit(3)")
    )
    job = _create(store)
    runner.submit(job["id"])
    failed = _wait(store, job["id"])
    assert failed["state"] == FAILED and "exit code 3" in failed["error"]


def test_runner_stops_an_analysis_at_the_time_limit(tmp_path):
    store = JobStore(tmp_path)
    settings = WebSettings(data_dir=tmp_path, job_timeout_seconds=1.0)
    runner = JobRunner(store, settings, command=_fake_worker("import time; time.sleep(60)"))
    job = _create(store)
    started = time.time()
    runner.submit(job["id"])
    failed = _wait(store, job["id"], timeout=15)
    assert failed["state"] == FAILED and "was stopped" in failed["error"]
    assert time.time() - started < 10


def test_runner_refuses_when_the_queue_is_full(tmp_path):
    store = JobStore(tmp_path)
    settings = WebSettings(data_dir=tmp_path, max_queued_jobs=1)
    runner = JobRunner(store, settings, command=_fake_worker("import time; time.sleep(2)"))
    first, second = _create(store), _create(store)
    runner.submit(first["id"])
    with pytest.raises(UploadRejected) as info:
        runner.submit(second["id"])
    assert info.value.status == 503
    json.dumps(store.read(first["id"]))  # state stays readable throughout

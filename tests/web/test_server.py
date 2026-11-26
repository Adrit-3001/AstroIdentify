"""The HTTP API end to end, with a fake worker (no solver, no network)."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astroidentify.web.jobs import JobRunner
from astroidentify.web.server import AppServer
from astroidentify.web.settings import WebSettings

FAKE_WORKER = """
import json, sys, time, pathlib, shutil
d = pathlib.Path(sys.argv[1]); (d / "result").mkdir()
src = next((d / "upload").glob("input*"))
shutil.copy(src, d / "result" / "original.png"); shutil.copy(src, d / "result" / "annotated.png")
(d / "result" / "result.json").write_text(json.dumps({"outcome": {"kind": "identified"}}))
job = json.loads((d / "job.json").read_text())
progress = {"stage": None, "completed": ["load"], "failed": None}
job.update(state="done", finished_ts=time.time(), progress=progress)
(d / "job.json").write_text(json.dumps(job))
"""


@pytest.fixture
def server(tmp_path):
    web_root = tmp_path / "site"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    settings = WebSettings(data_dir=tmp_path / "jobs", web_root=web_root, max_upload_bytes=200_000)
    store_dir = settings.data_dir

    def command(job_dir: Path, s: WebSettings) -> list[str]:
        return [sys.executable, "-c", FAKE_WORKER, str(job_dir)]

    from astroidentify.web.jobs import JobStore

    runner = JobRunner(JobStore(store_dir), settings, command=command)
    app = AppServer(("127.0.0.1", 0), settings, runner=runner)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{app.server_address[1]}"
    app.shutdown()
    app.server_close()


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _png_bytes(tmp_path) -> bytes:
    path = tmp_path / "x.png"
    Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(path)
    return path.read_bytes()


def test_config_reports_real_limits(server):
    status, headers, body = _request(f"{server}/api/config")
    config = json.loads(body)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert config["max_upload_bytes"] == 200_000
    assert [s["key"] for s in config["stages"]][:3] == ["load", "detect", "solve"]


def test_upload_returns_a_job_promptly_and_finishes(server, tmp_path):
    data = _png_bytes(tmp_path)
    started = time.time()
    status, _, body = _request(
        f"{server}/api/jobs",
        data,
        {"X-Filename": "My%20M57.png", "Content-Type": "application/octet-stream"},
    )
    assert status == 202 and time.time() - started < 5
    job_id = json.loads(body)["id"]
    for _ in range(200):
        status, _, body = _request(f"{server}/api/jobs/{job_id}")
        job = json.loads(body)
        if job["state"] == "done":
            break
        time.sleep(0.05)
    assert job["state"] == "done" and job["filename"] == "My M57.png"
    assert job["result"]["outcome"]["kind"] == "identified"
    status, headers, body = _request(f"{server}/api/jobs/{job_id}/annotated.png?download=1")
    assert status == 200 and body == data and headers["Content-Type"] == "image/png"
    assert headers["Content-Disposition"] == 'attachment; filename="My_M57-annotated.png"'


@pytest.mark.parametrize(
    ("name", "payload", "status"),
    [
        ("notes.txt", b"hello", 415),
        ("fake.png", b"hello, not a png", 415),
        (None, b"\x89PNG\r\n\x1a\n", 400),
    ],
)
def test_bad_uploads_are_refused_without_creating_jobs(server, tmp_path, name, payload, status):
    headers = {"X-Filename": name} if name else {}
    code, _, body = _request(f"{server}/api/jobs", payload, headers)
    assert code == status and json.loads(body)["error"]
    assert not any((tmp_path / "jobs").glob("*/job.json"))


def test_too_large_upload_is_refused(server):
    code, _, body = _request(
        f"{server}/api/jobs", b"\x89PNG\r\n\x1a\n" + b"\0" * 300_000, {"X-Filename": "big.png"}
    )
    assert code == 413 and "limit" in json.loads(body)["error"]


def test_unknown_and_malformed_jobs(server):
    assert _request(f"{server}/api/jobs/{'0' * 32}")[0] == 404
    assert _request(f"{server}/api/jobs/../../etc/passwd")[0] == 404
    assert _request(f"{server}/api/nothing")[0] == 404


def test_static_site_is_served(server):
    status, _, body = _request(f"{server}/")
    assert status == 200 and b"<title>t</title>" in body

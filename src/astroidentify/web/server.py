"""HTTP server for the web interface: a small JSON API plus the static site.

API (JSON unless noted):

* ``GET  /api/config``                       formats, size limit, retention
* ``POST /api/jobs``                         body = the file's bytes; header ``X-Filename``
                                             (URI-encoded). Answers 202 with the job at once.
* ``GET  /api/jobs/<id>``                    state, stage progress, queue place, result
* ``GET  /api/jobs/<id>/original.png``       the image as analysed (display orientation)
* ``GET  /api/jobs/<id>/view``               the result for display options (below)
* ``GET  /api/jobs/<id>/annotated.png``      the full-resolution annotated image
                                             (``?download=1`` saves it as a file)

Display options (query parameters of ``view`` and ``annotated.png``; see ``web.view``):
``types=galaxy,nebula,star`` (marker types to show; empty = none), ``brightest=N`` (name only
the N brightest) and ``label=NAME`` (repeatable: name these particular objects too). The
preview and the PNG for the same options are made from the same marker list.

Everything else is served from the static site directory. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from astroidentify.web.jobs import (
    FINAL_STATES,
    QUEUED,
    STAGES,
    JobRunner,
    JobStore,
    read_json,
    start_cleanup,
)
from astroidentify.web.settings import WebSettings
from astroidentify.web.validation import (
    UploadRejected,
    accepted_formats,
    check_request,
    display_name,
)
from astroidentify.web.view import TYPE_KEYS, ViewOptions, ViewOptionsError, make_view

logger = logging.getLogger(__name__)

_JOB_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})(?:/(original\.png|annotated\.png|view))?$")
_VIEW_PARAMS = ("types", "brightest", "label")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings: WebSettings, runner: JobRunner | None = None):
        self.settings = settings
        self.store = JobStore(settings.data_dir)
        self.store.mark_interrupted()
        self.runner = runner or JobRunner(self.store, settings)
        super().__init__(address, Handler)


class Handler(SimpleHTTPRequestHandler):
    server: AppServer
    server_version = "AstroIdentify"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(args[2].settings.web_root), **kwargs)

    # ----------------------------------------------------------------- plumbing

    def end_headers(self) -> None:
        # Always fresh: avoids a browser mixing old and new versions of the site's files.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_request(self, code="-", size="-") -> None:
        # Status polling would flood the log; keep uploads, errors and page loads.
        path = urlsplit(self.path).path
        if str(code) == "200" and _JOB_PATH.match(path) and self.command == "GET":
            return
        super().log_request(code, size)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # ----------------------------------------------------------------- routes

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/api/config":
            return self._config()
        match = _JOB_PATH.match(url.path)
        if match:
            job_id, asset = match.groups()
            params = parse_qs(url.query, keep_blank_values=True)
            if asset == "view":
                return self._view(job_id, params)
            if asset:
                return self._asset(job_id, asset, params)
            return self._status(job_id)
        if url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
        return super().do_GET()

    def do_HEAD(self) -> None:
        if urlsplit(self.path).path.startswith("/api/"):
            return self.do_GET()
        return super().do_HEAD()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/jobs":
            return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
        try:
            job = self._receive_upload()
            position = self.server.runner.submit(job["id"])
        except UploadRejected as exc:
            # The body may be unread (e.g. too large): answer, then drop the connection.
            self.close_connection = True
            return self._error(exc.status, str(exc))
        logger.info("job %s queued (%s)", job["id"], job["filename"])
        self._json(
            HTTPStatus.ACCEPTED,
            {"id": job["id"], "state": job["state"], "queue_ahead": max(0, position - 1)},
        )

    # ----------------------------------------------------------------- handlers

    def _config(self) -> None:
        s = self.server.settings
        self._json(
            HTTPStatus.OK,
            {
                "formats": accepted_formats(),
                "max_upload_bytes": s.max_upload_bytes,
                "retention_hours": s.retention_hours,
                "stages": [{"key": k, "label": label} for k, label in STAGES],
                "view_types": list(TYPE_KEYS),
            },
        )

    def _receive_upload(self) -> dict[str, Any]:
        raw_name = self.headers.get("X-Filename")
        filename = unquote(raw_name) if raw_name else None
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length is not None else None
        except ValueError:
            size = None
        ext = check_request(filename, size, self.server.settings.max_upload_bytes)
        return self.server.store.create(display_name(filename), ext, self.rfile, size)

    def _status(self, job_id: str) -> None:
        job = self.server.store.read(job_id)
        if job is None:
            return self._error(
                HTTPStatus.NOT_FOUND,
                "This analysis doesn't exist or has expired "
                f"(results are kept for {self.server.settings.retention_hours:g} hours).",
            )
        payload = {
            "id": job["id"],
            "filename": job.get("filename"),
            "state": job["state"],
            "progress": job.get("progress"),
            "error": job.get("error"),
            "elapsed_seconds": _elapsed(job),
            "queue_ahead": None,
            "result": None,
        }
        if job["state"] == QUEUED:
            position = self.server.runner.position(job_id)
            payload["queue_ahead"] = max(0, position - 1) if position else 0
        if job["state"] in FINAL_STATES:
            directory = self.server.store.directory(job_id)
            payload["result"] = read_json(directory / "result" / "result.json")
        self._json(HTTPStatus.OK, payload)

    def _options_view(self, job_id: str, params: dict[str, list[str]]):
        """``(view, directory)`` for the display options in ``params``, or ``None`` after
        answering with an error."""
        try:
            options = ViewOptions.from_query(params)
        except ViewOptionsError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"Invalid display options: {exc}.")
            return None
        directory = self.server.store.directory(job_id)
        analysis = read_json(directory / "result" / "analysis.json") if directory else None
        if analysis is None:
            message = "This analysis doesn't exist or has expired."
            if directory and (directory / "result" / "result.json").is_file():
                message = (
                    "This analysis was made by an earlier version of AstroIdentify, so its "
                    "display options can't be changed. Analyze the image again to use them."
                )
            self._error(HTTPStatus.NOT_FOUND, message)
            return None
        return make_view(analysis, options), directory

    def _view(self, job_id: str, params: dict[str, list[str]]) -> None:
        made = self._options_view(job_id, params)
        if made is not None:
            self._json(HTTPStatus.OK, made[0])

    def _asset(self, job_id: str, asset: str, params: dict[str, list[str]]) -> None:
        directory = self.server.store.directory(job_id)
        path = directory / "result" / asset if directory else None
        if path is None or not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "This image isn't available.")
        if asset == "annotated.png" and any(k in params for k in _VIEW_PARAMS):
            made = self._options_view(job_id, params)
            if made is None:
                return None
            data = _render_png(made[0], directory / "result" / "original.png")
        else:
            data = path.read_bytes()
        download = "download" in params
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        if download:
            job = self.server.store.read(job_id) or {}
            stem = Path(job.get("filename") or "image").stem.split(".")[0] or "image"
            suffix = "annotated" if asset.startswith("annotated") else "original"
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{stem}-{suffix}.png")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def _render_png(view: dict[str, Any], original: Path) -> bytes:
    """The annotated PNG for a view, drawn on the saved original (display orientation)."""
    import io

    import numpy as np
    from PIL import Image

    from astroidentify.web.annotations import render_annotated

    with Image.open(original) as image:
        pixels = np.asarray(image)
    buffer = io.BytesIO()
    render_annotated(pixels, view).save(buffer, format="PNG")
    return buffer.getvalue()


def _elapsed(job: dict[str, Any]) -> float | None:
    start = job.get("started_ts")
    if not start:
        return None
    end = job.get("finished_ts") or time.time()
    return round(end - start, 1)


def serve(settings: WebSettings, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the web interface until interrupted."""
    if not (Path(settings.web_root) / "index.html").is_file():
        raise SystemExit(f"astroidentify serve: static site not found at {settings.web_root}")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    server = AppServer((host, port), settings)
    start_cleanup(server.store, settings)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(
        f"AstroIdentify is running at http://{shown}:{port}/  (analyze: /analyze.html)\n"
        f"Jobs: {server.store.root}  (kept {settings.retention_hours:g} h after finishing)\n"
        "Press Ctrl+C to stop.",
        file=sys.stderr,
    )
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        server.server_close()

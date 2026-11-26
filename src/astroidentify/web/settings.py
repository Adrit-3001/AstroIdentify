"""Settings for the web interface (all overridable from ``astroidentify serve``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: The static site (landing page and workspace), next to the package in a source checkout.
DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[3] / "web"


@dataclass(frozen=True)
class WebSettings:
    """Web interface settings.

    Attributes:
        data_dir: Where job directories live (one per upload). Inside ``outputs/`` by default,
            which git ignores.
        catalog_cache_dir: Gaia/SIMBAD response cache, shared with the CLI's default cache.
        web_root: Static files to serve (landing page, workspace, scripts).
        max_upload_bytes: Largest accepted upload.
        retention_hours: Finished jobs (upload, results, annotated image) are deleted this long
            after they finish. Checked at start-up and every ``cleanup_interval_seconds``.
        cleanup_interval_seconds: How often expired jobs are removed.
        job_timeout_seconds: Hard limit for one analysis; the worker is stopped after this.
        solve_attempt_timeout_seconds: Plate-solver limit per attempt (``solve --timeout``).
        solve_total_timeout_seconds: Plate-solver limit for all attempts
            (``solve --total-timeout``).
        max_queued_jobs: Uploads refused (HTTP 503) while this many jobs are waiting.
    """

    data_dir: Path = Path("outputs/web-jobs")
    catalog_cache_dir: Path = Path("outputs/.catalog-cache")
    web_root: Path = field(default_factory=lambda: DEFAULT_WEB_ROOT)
    max_upload_bytes: int = 64 * 1024 * 1024
    retention_hours: float = 24.0
    cleanup_interval_seconds: float = 3600.0
    job_timeout_seconds: float = 30 * 60.0
    solve_attempt_timeout_seconds: float = 300.0
    solve_total_timeout_seconds: float = 900.0
    max_queued_jobs: int = 10

    @property
    def retention_seconds(self) -> float:
        return self.retention_hours * 3600.0

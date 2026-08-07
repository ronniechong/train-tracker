"""Prometheus textfile-collector output for the archiver.

The archiver is a one-shot batch job (`docker compose run --rm`, exits
after each pass) -- unlike `poller`, it's never up long enough for
Prometheus to scrape it directly. This writes the node_exporter textfile
collector format instead: a plain `.prom` file to a directory the shared
host's `node-exporter` container watches (ops-side wiring, see
`deploy/docker-compose.yml`'s `archiver` service comments and the
private ops notes). node_exporter re-reads the directory on its own
scrape interval, so metrics here are "as of the last completed run", not
truly live -- acceptable for a job that only runs once a night.

Written atomically (temp file + rename) -- textfile collector's own docs
warn a scrape mid-write can otherwise see a truncated/invalid file.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .run import ArchiveRunResult

_HELP = {
    "archive_last_run_timestamp_seconds": "Unix timestamp of the last completed archiver pass (runs even if some days failed).",
    "archive_days_pending": "Closed local days not yet fully archived, as of the last pass.",
    "archive_days_pending_oldest_age_days": "Age in days of the oldest currently-pending day (0 if none pending).",
    "archive_upload_retry_failures_total": "Upload attempt failures (including ones that eventually succeeded on retry) in the last pass.",
}


def render_textfile(result: ArchiveRunResult, now: datetime) -> str:
    oldest_age = 0
    if result.failed:
        oldest_age = max((now.date() - d).days for d in result.failed)

    lines: list[str] = []
    for metric, help_text in _HELP.items():
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")

    lines.append(f"archive_last_run_timestamp_seconds {now.timestamp()}")
    lines.append(f"archive_days_pending {len(result.failed)}")
    lines.append(f"archive_days_pending_oldest_age_days {oldest_age}")
    lines.append(f"archive_upload_retry_failures_total {result.upload_retry_failures}")
    return "\n".join(lines) + "\n"


def write_textfile_metrics(path: Path, result: ArchiveRunResult, now: datetime) -> None:
    """Atomic write: node_exporter's textfile collector can otherwise
    scrape a partially-written file mid-update."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(render_textfile(result, now))
    os.replace(tmp_path, path)

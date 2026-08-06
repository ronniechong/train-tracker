"""Queryable/exportable failure & gap report for the archive pipeline.
Plain JSON-lines file, one entry per event -- append-only, matching this
project's existing preference for simple, inspectable file formats over a
database for low-volume operational logs.

Lives on its own small persistent bind mount (`/archive-state`) -- the
archiver's `/data` mount is read-only (partitions only) and `/staging` is
ephemeral scratch, so the report needs a home that survives container
restarts independently of either. It's also the only local state this
container needs at all; catch-up itself works by diffing against Hugging
Face's own file listing, no state file required for that.

Pruned to the last `REPORT_RETENTION_DAYS` (~6 months) by `detected_at`
age, not `service_date` -- an old, once-permanent gap is still worth
keeping visible near its original detection time, but the report itself
shouldn't grow forever on a project with only a 60-day raw data retention
window.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

REPORT_RETENTION_DAYS = 182  # ~6 months


@dataclass(frozen=True)
class GapReportEntry:
    service_date: date
    detected_at: datetime
    reason: str
    recovered: bool  # True if a backup-restore attempt fixed it this run
    permanent: bool  # True once past the 23-day safety-net age with no recovery

    def to_json(self) -> str:
        row = asdict(self)
        row["service_date"] = self.service_date.isoformat()
        row["detected_at"] = self.detected_at.isoformat()
        return json.dumps(row)

    @staticmethod
    def from_json(line: str) -> "GapReportEntry":
        row = json.loads(line)
        return GapReportEntry(
            service_date=date.fromisoformat(row["service_date"]),
            detected_at=datetime.fromisoformat(row["detected_at"]),
            reason=row["reason"],
            recovered=row["recovered"],
            permanent=row["permanent"],
        )


def append_gap_report(report_path: Path, entry: GapReportEntry) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("a") as f:
        f.write(entry.to_json() + "\n")


def read_gap_report(report_path: Path) -> list[GapReportEntry]:
    if not report_path.exists():
        return []
    with report_path.open() as f:
        return [GapReportEntry.from_json(line) for line in f if line.strip()]


def prune_gap_report(
    report_path: Path, now: datetime, retention_days: int = REPORT_RETENTION_DAYS
) -> int:
    """Drop entries older than `retention_days` (by `detected_at`).
    Returns the number of entries removed. A no-op, not an error, if the
    report doesn't exist yet."""
    entries = read_gap_report(report_path)
    if not entries:
        return 0
    cutoff = now - timedelta(days=retention_days)
    kept = [e for e in entries if e.detected_at >= cutoff]
    removed = len(entries) - len(kept)
    if removed:
        with report_path.open("w") as f:
            for entry in kept:
                f.write(entry.to_json() + "\n")
    return removed

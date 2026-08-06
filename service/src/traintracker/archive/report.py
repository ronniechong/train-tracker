"""Queryable/exportable failure & gap report (milestone 09's "Failure/gap
report" task, 2026-08-07). Plain JSON-lines file, one entry per event --
append-only, matching this project's existing preference for simple,
inspectable file formats over a database for low-volume operational logs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path


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

"""Orchestrates one archiver pass: find closed local days not yet fully
archived, self-heal missing/corrupt partitions from backup where possible,
compact + drift-check + stage + upload each one, and report anything that
couldn't be recovered.

Self-healing: the only recovery source is the
local nightly backup copy -- no upstream archive exists for this data, so
"rebuild from source" always means "restore from backup," never a true
upstream rewind. If backup is also missing/corrupt, the day is logged via
`report.py` and left for the next run to retry; once its age exceeds the
23-day safety-net threshold with no recovery, it's marked permanent.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

from ..history.retention import RETENTION_DAYS, is_partition_closed, partition_service_date
from .compact import compact_partition
from .drift import DriftFinding, detect_drift
from .report import GapReportEntry, append_gap_report
from .upload import UploadStats, archived_days, is_day_fully_archived, upload_day
from .write import write_staged_parquet

logger = logging.getLogger("traintracker.archive.run")

# 23-day buffer before the 60-day retention window could delete a day that
# was never successfully archived -- decoupled from retention's own cron,
# not a shared constant with it.
SAFETY_NET_DAYS = RETENTION_DAYS - 7


@dataclass(frozen=True)
class ArchiveRunResult:
    archived: tuple[date, ...]
    failed: tuple[date, ...]
    recovered_from_backup: tuple[date, ...]
    drift_findings: tuple[DriftFinding, ...]
    upload_retry_failures: int


def _closed_local_days(history_dir: Path, backup_dir: Path, now: datetime) -> list[date]:
    """Union of both directories -- a day that's missing from `history_dir`
    entirely (not just corrupt) but still present in `backup_dir` must
    still be a self-healing candidate, not silently invisible to this scan."""
    days: set[date] = set()
    for directory in (history_dir, backup_dir):
        for path in sorted(directory.glob("*.db")):
            try:
                service_date = partition_service_date(path)
            except ValueError:
                continue  # not one of our partition files
            if is_partition_closed(service_date, now):
                days.add(service_date)
    return sorted(days)


def _try_compact(
    history_dir: Path, backup_dir: Path, service_date: date
) -> tuple[dict[str, pa.Table] | None, bool]:
    """Returns (tables, recovered_from_backup). `tables` is None only if
    both the live partition and its backup copy are missing or corrupt."""
    path = history_dir / f"{service_date.isoformat()}.db"
    try:
        return compact_partition(path), False
    except sqlite3.Error:
        pass

    backup_path = backup_dir / path.name
    if not backup_path.exists():
        return None, False
    try:
        shutil.copy2(backup_path, path)
        return compact_partition(path), True
    except sqlite3.Error:
        return None, False


def run_archive_pass(
    *,
    history_dir: Path,
    backup_dir: Path,
    staging_dir: Path,
    report_path: Path,
    repo_id: str,
    token: str,
    now: datetime,
) -> ArchiveRunResult:
    archived = archived_days(repo_id, token)
    closed_days = _closed_local_days(history_dir, backup_dir, now)

    archived_ok: list[date] = []
    failed: list[date] = []
    recovered: list[date] = []
    drift_findings: list[DriftFinding] = []
    upload_stats = UploadStats()

    for service_date in closed_days:
        if is_day_fully_archived(archived, service_date):
            continue

        tables, was_recovered = _try_compact(history_dir, backup_dir, service_date)

        if tables is None:
            age_days = (now.date() - service_date).days
            permanent = age_days > SAFETY_NET_DAYS
            append_gap_report(
                report_path,
                GapReportEntry(
                    service_date=service_date, detected_at=now,
                    reason="missing_or_corrupt_partition_and_backup",
                    recovered=False, permanent=permanent,
                ),
            )
            if permanent:
                logger.error("permanent gap for service_date=%s (age=%dd)", service_date, age_days)
            else:
                logger.warning("archive failed for service_date=%s, will retry next run", service_date)
            failed.append(service_date)
            continue

        if was_recovered:
            append_gap_report(
                report_path,
                GapReportEntry(
                    service_date=service_date, detected_at=now,
                    reason="restored_from_backup", recovered=True, permanent=False,
                ),
            )
            recovered.append(service_date)
            logger.info("self-healed service_date=%s from backup", service_date)

        findings = detect_drift(tables)
        for finding in findings:
            logger.warning(
                "drift: %s.%s has unknown values %s",
                finding.table, finding.column, sorted(finding.unknown_values),
            )
        drift_findings.extend(findings)

        staged = write_staged_parquet(tables, staging_dir, service_date)
        upload_day(repo_id, token, staged, service_date, stats=upload_stats)
        archived_ok.append(service_date)
        logger.info("archived service_date=%s", service_date)

    return ArchiveRunResult(
        archived=tuple(archived_ok),
        failed=tuple(failed),
        recovered_from_backup=tuple(recovered),
        drift_findings=tuple(drift_findings),
        upload_retry_failures=upload_stats.retry_failures,
    )

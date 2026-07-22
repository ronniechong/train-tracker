"""The nightly maintenance job: sync closed partitions to backup, then apply
60-day retention to both directories.

Retention on `history_dir` requires the file already be present in
`backup_dir` first (see `retention.py`); `backup_dir` is pruned on the same
rule independently. Built as a plain callable, same as 2c's `refresh_and_pin`
precedent — host-level cron wiring is a separate ops step, not built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .retention import RETENTION_DAYS, RetentionResult, apply_retention
from .sync import SyncResult, sync_closed_partitions


@dataclass(frozen=True)
class NightlyResult:
    sync: SyncResult
    history_retention: RetentionResult
    backup_retention: RetentionResult


def run_nightly_maintenance(
    history_dir: Path,
    backup_dir: Path,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> NightlyResult:
    now = now or datetime.now(timezone.utc)
    today = now.date()

    sync_result = sync_closed_partitions(history_dir, backup_dir, now)
    history_retention = apply_retention(
        history_dir, today, retention_days, require_present_in=backup_dir,
    )
    backup_retention = apply_retention(backup_dir, today, retention_days)

    return NightlyResult(
        sync=sync_result,
        history_retention=history_retention,
        backup_retention=backup_retention,
    )

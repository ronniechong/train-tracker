"""Nightly backup sync: copy closed-but-not-yet-backed-up partition files
from the live history directory to a second directory.

Copies rather than moves — the live directory stays the working copy until
`retention.py` independently prunes it once a partition ages past 60 days.
Sync and retention are two separate, composable steps, not one fused "move"
operation, so an aborted or failed retention pass never risks a partition
that hasn't actually been backed up yet.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .retention import is_partition_closed, partition_service_date


@dataclass(frozen=True)
class SyncResult:
    synced: tuple[Path, ...]


def sync_closed_partitions(history_dir: Path, backup_dir: Path, now: datetime) -> SyncResult:
    backup_dir.mkdir(parents=True, exist_ok=True)
    synced: list[Path] = []

    for path in sorted(history_dir.glob("*.db")):
        try:
            service_date = partition_service_date(path)
        except ValueError:
            continue  # not one of our partition files; leave it alone

        if not is_partition_closed(service_date, now):
            continue  # still open (or only just closed) — may still receive writes

        dest = backup_dir / path.name
        if dest.exists():
            continue  # already synced; closed partitions are immutable

        shutil.copy2(path, dest)
        synced.append(dest)

    return SyncResult(synced=tuple(synced))

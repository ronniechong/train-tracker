"""Closed-partition definition and 60-day retention for day-partitioned
history files (see `store.py`'s module docstring for the overall design).

Deliberately generic over "a directory of `{service_date}.db` files" so the
exact same rule applies independently to both the live history directory and
the backup directory (see `nightly.py`) — retention is not a special case of
sync, it's the same function called twice with different arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..gtfs.gtfstime import service_date_boundary_utc

# Confirmed by Ronnie 2026-07-20 (CLAUDE.md's settled "History scope" decision).
RETENTION_DAYS = 60

# Small margin past the exact `service_date_for_instant` rollover boundary —
# guards only against clock skew / last-cycle lag right at the boundary
# instant. The boundary itself (not this buffer) is what makes a 24:xx trip's
# writes land in the right partition; see `gtfstime.py`.
CLOSE_BUFFER = timedelta(minutes=30)


def partition_service_date(path: Path) -> date:
    """Parse the service_date a `{service_date}.db` partition file belongs
    to from its filename. Raises ValueError for anything not shaped like
    one of our own partition files (callers should skip those, not crash)."""
    return date.fromisoformat(path.stem)


def is_partition_closed(
    service_date: date, now: datetime, buffer: timedelta = CLOSE_BUFFER
) -> bool:
    """True once no further write for `service_date` is possible: `now` is
    past the instant at which `service_date_for_instant` starts attributing
    observations to the *next* service_date, plus a small buffer."""
    next_day = date.fromordinal(service_date.toordinal() + 1)
    return now >= service_date_boundary_utc(next_day) + buffer


@dataclass(frozen=True)
class RetentionResult:
    deleted: tuple[Path, ...]
    # Only ever populated when `require_present_in` is given.
    skipped_not_backed_up: tuple[Path, ...]


def apply_retention(
    directory: Path,
    today: date,
    retention_days: int = RETENTION_DAYS,
    require_present_in: Path | None = None,
) -> RetentionResult:
    """Delete `{service_date}.db` files in `directory` whose service_date is
    more than `retention_days` behind `today`.

    `require_present_in`, when given, is a second directory (the backup
    destination) a file must already exist in before it's deleted from
    `directory` — guards against a blind age-based delete silently losing
    data if the nightly sync step ever fails before retention runs. Calling
    this again on the backup directory itself (with `require_present_in`
    left unset) prunes it independently on the same rule.
    """
    deleted: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(directory.glob("*.db")):
        try:
            service_date = partition_service_date(path)
        except ValueError:
            continue  # not one of our partition files; leave it alone

        if (today - service_date).days <= retention_days:
            continue

        if require_present_in is not None:
            if not (require_present_in / path.name).exists():
                skipped.append(path)
                continue

        path.unlink()
        deleted.append(path)

    return RetentionResult(deleted=tuple(deleted), skipped_not_backed_up=tuple(skipped))

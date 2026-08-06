"""Persistence for the weekly performance digest.

Deliberately separate from `history/store.py`'s `HistoryStore`: that store
is day-partitioned raw events with a 60-day rolling retention cap; this is
a small number of already-aggregated summaries (~52/year) that need
INDEFINITE retention -- a public "log" implies a growing track record, not
a window that quietly loses history. One connection, one file, opened once
and held for the process lifetime -- unlike `HistoryStore` there is no
daily rotation here at all.

Trigger idempotency (has this week's digest already been sent) is
deliberately NOT this module's job -- `poller/weekly_digest_trigger.py`
owns its own small JSON sidecar for that, following `gtfs/pinning.py`'s
`PinManifest` pattern, keeping this store scoped to digest content only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class LineStat:
    """One line's breakdown within a single week's digest. Trips below the
    per-line minimum-sample-size floor (20/week) are never turned into a
    `LineStat` at all by the aggregation step (`ai/weekly_digest.py`) --
    this store persists whatever it's given, it doesn't apply the floor
    itself."""

    route_id: str
    trip_count: int
    on_time_count: int
    late_count: int
    cancelled_count: int
    on_time_pct: float  # of trips that ran on this line (cancelled excluded from the denominator)


@dataclass(frozen=True)
class WeeklyDigestRecord:
    """Everything about one week's digest except its storage-assigned id
    and generated_at -- what a caller has in hand right after computing
    and narrating a digest, before it's been persisted."""

    week_start: date
    week_end: date
    days_covered: int  # out of 7 -- honest partial-window reporting (cold start / gap days)
    on_time_count: int
    late_count: int
    cancelled_count: int
    on_time_pct: float  # of trips that ran (cancelled excluded from the denominator)
    narrative: str
    slack_delivered: bool
    line_stats: tuple[LineStat, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StoredWeeklyDigest:
    """A `WeeklyDigestRecord` plus what only the store can assign --
    `PinResult` wrapping `Pin` in `gtfs/pinning.py` is the precedent for
    this "id/timestamp wraps the value" shape."""

    id: int
    generated_at: datetime
    record: WeeklyDigestRecord


_CREATE_DIGESTS_SQL = """
    CREATE TABLE IF NOT EXISTS weekly_digests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        week_end TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        days_covered INTEGER NOT NULL,
        on_time_count INTEGER NOT NULL,
        late_count INTEGER NOT NULL,
        cancelled_count INTEGER NOT NULL,
        on_time_pct REAL NOT NULL,
        narrative TEXT NOT NULL,
        slack_delivered INTEGER NOT NULL
    )
"""

_CREATE_LINE_STATS_SQL = """
    CREATE TABLE IF NOT EXISTS weekly_digest_line_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        digest_id INTEGER NOT NULL REFERENCES weekly_digests(id),
        route_id TEXT NOT NULL,
        trip_count INTEGER NOT NULL,
        on_time_count INTEGER NOT NULL,
        late_count INTEGER NOT NULL,
        cancelled_count INTEGER NOT NULL,
        on_time_pct REAL NOT NULL
    )
"""


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


class WeeklyDigestStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit, matching HistoryStore's reasoning: write volume
        # here is once a week, so per-statement commit overhead is
        # irrelevant, and a crash mid-write can never leave an uncommitted
        # row silently lost.
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute(_CREATE_DIGESTS_SQL)
        self._conn.execute(_CREATE_LINE_STATS_SQL)

    def record(self, digest: WeeklyDigestRecord) -> StoredWeeklyDigest:
        generated_at = datetime.now(timezone.utc)
        cursor = self._conn.execute(
            """
            INSERT INTO weekly_digests
                (week_start, week_end, generated_at, days_covered,
                 on_time_count, late_count, cancelled_count, on_time_pct,
                 narrative, slack_delivered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                digest.week_start.isoformat(),
                digest.week_end.isoformat(),
                generated_at.isoformat(),
                digest.days_covered,
                digest.on_time_count,
                digest.late_count,
                digest.cancelled_count,
                digest.on_time_pct,
                digest.narrative,
                _bool_to_int(digest.slack_delivered),
            ),
        )
        digest_id = cursor.lastrowid
        for line in digest.line_stats:
            self._conn.execute(
                """
                INSERT INTO weekly_digest_line_stats
                    (digest_id, route_id, trip_count, on_time_count,
                     late_count, cancelled_count, on_time_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest_id,
                    line.route_id,
                    line.trip_count,
                    line.on_time_count,
                    line.late_count,
                    line.cancelled_count,
                    line.on_time_pct,
                ),
            )
        return StoredWeeklyDigest(id=digest_id, generated_at=generated_at, record=digest)

    def list_digests(self, limit: int = 20) -> list[StoredWeeklyDigest]:
        """Most recent week first. N+1 line-stat queries per digest --
        fine at this scale (~52 digests/year, single-digit line rows each),
        not worth a join for the row counts involved."""
        rows = self._conn.execute(
            """
            SELECT id, week_start, week_end, generated_at, days_covered,
                   on_time_count, late_count, cancelled_count, on_time_pct,
                   narrative, slack_delivered
            FROM weekly_digests
            ORDER BY week_start DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._to_stored_digest(row) for row in rows]

    def _to_stored_digest(self, row: tuple) -> StoredWeeklyDigest:
        (digest_id, week_start, week_end, generated_at, days_covered,
         on_time_count, late_count, cancelled_count, on_time_pct,
         narrative, slack_delivered) = row
        line_rows = self._conn.execute(
            """
            SELECT route_id, trip_count, on_time_count, late_count,
                   cancelled_count, on_time_pct
            FROM weekly_digest_line_stats
            WHERE digest_id = ?
            ORDER BY route_id
            """,
            (digest_id,),
        ).fetchall()
        line_stats = tuple(
            LineStat(
                route_id=r[0], trip_count=r[1], on_time_count=r[2],
                late_count=r[3], cancelled_count=r[4], on_time_pct=r[5],
            )
            for r in line_rows
        )
        return StoredWeeklyDigest(
            id=digest_id,
            generated_at=datetime.fromisoformat(generated_at),
            record=WeeklyDigestRecord(
                week_start=date.fromisoformat(week_start),
                week_end=date.fromisoformat(week_end),
                days_covered=days_covered,
                on_time_count=on_time_count,
                late_count=late_count,
                cancelled_count=cancelled_count,
                on_time_pct=on_time_pct,
                narrative=narrative,
                slack_delivered=bool(slack_delivered),
                line_stats=line_stats,
            ),
        )

    def close(self) -> None:
        self._conn.close()

"""SQLite-backed persistence for 2d's three `EventLog` outputs
(`DiscrepancyEvent`, `GhostEvent`, `PollGapEvent`) plus 05-ai-layer's
`TripCompletionEvent` and `DelayObservationEvent` — the concrete
implementation `state/eventlog.py` forward-references as "2e ... owns a
SQLite-backed implementation of this same Protocol."

One SQLite file per service_date, not per event type: `discrepancy_events`,
`ghost_events`, `poll_gap_events`, `trip_completion_events`, and
`delay_observation_events` all live in the same file, alongside a `meta`
row recording which static-snapshot digest (2c's `PinManifest`) was pinned
to that service_date at the time the partition was opened — this is the
"paired with that day's pinned static snapshot" requirement from milestone
2e. `delay_observation_events` (2026-08-01) deliberately keeps the SAME
60-day rolling retention as everything else here, unlike the weekly
digest's separate `WeeklyDigestStore` -- this data's value decays as soon
as a fresher model has been retrained on more recent conditions, so the
existing cap is the right fit, not an exception to it.

Routing a `.record(event)` call to the correct day's file is `rotate(now)`'s
job, called once per poll cycle by the caller (`poller/__main__.py`) — this
is deliberate: nothing in `merge.py`, `ghost.py`, or `breaker.py` needs to
change or know partitioning exists at all, matching `eventlog.py`'s own
promise that upstream callers of `EventLog.record()` are unaffected.

Plain rollback-journal mode (not WAL): write volume here is occasional
episodes, not per-cycle, and a closed partition is then a single
self-contained file with no `-wal`/`-shm` sidecars for `sync.py`'s plain
`shutil.copy2` to miss.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from ..gtfs.gtfstime import service_date_for_instant
from ..gtfs.pinning import PinManifest
from ..poller.breaker import PollGapEvent
from ..state.completion import TripCompletionEvent
from ..state.delay_observation import DelayObservationEvent
from ..state.ghost import GhostEvent
from ..state.merge import DiscrepancyEvent


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _delay_observation_row(event: DelayObservationEvent) -> tuple:
    return (
        datetime.now(timezone.utc).isoformat(),
        event.trip_id,
        event.route_id,
        event.service_date,
        _iso(event.observed_at),
        event.current_delay_s,
        event.stops_remaining,
        _bool_to_int(event.active_alert_flag),
    )


@dataclass(frozen=True)
class _TableSpec:
    name: str
    create_sql: str
    insert_sql: str
    to_row: Callable[[object], tuple]


def _discrepancy_row(event: DiscrepancyEvent) -> tuple:
    return (
        datetime.now(timezone.utc).isoformat(),
        event.trip_id,
        _iso(event.observed_at),
        event.discrepancy_type,
        event.tu_value,
        event.vp_value,
    )


def _ghost_row(event: GhostEvent) -> tuple:
    last_lat, last_lon = event.last_seen_position or (None, None)
    reappear_lat, reappear_lon = event.reappear_position or (None, None)
    return (
        datetime.now(timezone.utc).isoformat(),
        event.trip_id,
        _iso(event.last_seen_at),
        last_lat,
        last_lon,
        _iso(event.reappeared_at),
        reappear_lat,
        reappear_lon,
        _bool_to_int(event.loop_contained),
        event.ghost_duration_s,
        _bool_to_int(event.backoff_overlapped),
    )


def _poll_gap_row(event: PollGapEvent) -> tuple:
    return (
        datetime.now(timezone.utc).isoformat(),
        _iso(event.started_at),
        _iso(event.ended_at),
        event.reason,
        event.consecutive_failures,
        event.max_level_reached_s,
    )


def _trip_completion_row(event: TripCompletionEvent) -> tuple:
    return (
        datetime.now(timezone.utc).isoformat(),
        event.trip_id,
        event.route_id,
        event.service_date,
        _iso(event.scheduled_terminus_arrival),
        _iso(event.actual_terminus_arrival),
        event.delay_seconds,
        event.status,
    )


DISCREPANCY_TABLE = _TableSpec(
    name="discrepancy_events",
    create_sql="""
        CREATE TABLE IF NOT EXISTS discrepancy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            discrepancy_type TEXT NOT NULL,
            tu_value TEXT,
            vp_value TEXT
        )
    """,
    insert_sql="""
        INSERT INTO discrepancy_events
            (recorded_at, trip_id, observed_at, discrepancy_type, tu_value, vp_value)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
    to_row=_discrepancy_row,
)

GHOST_TABLE = _TableSpec(
    name="ghost_events",
    create_sql="""
        CREATE TABLE IF NOT EXISTS ghost_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            last_seen_at TEXT,
            last_seen_lat REAL,
            last_seen_lon REAL,
            reappeared_at TEXT,
            reappear_lat REAL,
            reappear_lon REAL,
            loop_contained INTEGER NOT NULL,
            ghost_duration_s REAL,
            backoff_overlapped INTEGER NOT NULL
        )
    """,
    insert_sql="""
        INSERT INTO ghost_events
            (recorded_at, trip_id, last_seen_at, last_seen_lat, last_seen_lon,
             reappeared_at, reappear_lat, reappear_lon, loop_contained,
             ghost_duration_s, backoff_overlapped)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    to_row=_ghost_row,
)

POLL_GAP_TABLE = _TableSpec(
    name="poll_gap_events",
    create_sql="""
        CREATE TABLE IF NOT EXISTS poll_gap_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            max_level_reached_s REAL NOT NULL
        )
    """,
    insert_sql="""
        INSERT INTO poll_gap_events
            (recorded_at, started_at, ended_at, reason, consecutive_failures, max_level_reached_s)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
    to_row=_poll_gap_row,
)

TRIP_COMPLETION_TABLE = _TableSpec(
    name="trip_completion_events",
    create_sql="""
        CREATE TABLE IF NOT EXISTS trip_completion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            route_id TEXT,
            service_date TEXT NOT NULL,
            scheduled_terminus_arrival TEXT NOT NULL,
            actual_terminus_arrival TEXT,
            delay_seconds INTEGER,
            status TEXT NOT NULL
        )
    """,
    insert_sql="""
        INSERT INTO trip_completion_events
            (recorded_at, trip_id, route_id, service_date, scheduled_terminus_arrival,
             actual_terminus_arrival, delay_seconds, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    to_row=_trip_completion_row,
)

DELAY_OBSERVATION_TABLE = _TableSpec(
    name="delay_observation_events",
    create_sql="""
        CREATE TABLE IF NOT EXISTS delay_observation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            route_id TEXT,
            service_date TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            current_delay_s INTEGER NOT NULL,
            stops_remaining INTEGER NOT NULL,
            active_alert_flag INTEGER NOT NULL
        )
    """,
    insert_sql="""
        INSERT INTO delay_observation_events
            (recorded_at, trip_id, route_id, service_date, observed_at,
             current_delay_s, stops_remaining, active_alert_flag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    to_row=_delay_observation_row,
)

_ALL_TABLES = (
    DISCREPANCY_TABLE, GHOST_TABLE, POLL_GAP_TABLE, TRIP_COMPLETION_TABLE,
    DELAY_OBSERVATION_TABLE,
)


def _row_to_completion_event(row: tuple) -> TripCompletionEvent:
    trip_id, route_id, service_date, scheduled_arrival, actual_arrival, delay_seconds, status = row
    return TripCompletionEvent(
        trip_id=trip_id,
        route_id=route_id,
        service_date=service_date,
        scheduled_terminus_arrival=_parse_iso(scheduled_arrival),
        actual_terminus_arrival=_parse_iso(actual_arrival),
        delay_seconds=delay_seconds,
        status=status,
    )


@dataclass(frozen=True)
class CompletionEventsWindow:
    """The result of reading `trip_completion_events` across multiple
    day-partitions at once (05-ai-layer's weekly digest -- the first
    feature needing this; see milestones/05-ai-layer.md). `days_covered`
    vs `days_missing` is the honesty distinction the digest's cold-start
    and gap-day handling depends on: a partition file existing (however
    few events it holds) means genuine coverage; a missing file means an
    unknown -- the poller wasn't running that service_date at all, not
    "zero trips ran"."""

    events: tuple[TripCompletionEvent, ...]
    days_covered: tuple[date, ...]
    days_missing: tuple[date, ...]

_META_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS meta (
        service_date TEXT PRIMARY KEY,
        static_snapshot_digest TEXT,
        opened_at TEXT NOT NULL
    )
"""


class _TableEventLog:
    """`EventLog`-Protocol facade for one table, bound to a `HistoryStore` so
    `.record(event)` always lands in whichever partition is currently open."""

    def __init__(self, store: "HistoryStore", spec: _TableSpec):
        self._store = store
        self._spec = spec

    def record(self, event: object) -> None:
        self._store._insert(self._spec, self._spec.to_row(event))


class HistoryStore:
    """Owns the single SQLite connection for whichever service_date is
    currently open. Hand `.discrepancy_log`, `.ghost_log`, and `.gap_log`
    straight to `StateStore`/`PollerLoop` in place of the `InMemoryEventLog`s
    they use today — nothing else about their construction changes."""

    def __init__(self, history_dir: Path, pin_manifest: PinManifest | None = None):
        self._history_dir = history_dir
        self._pin_manifest = pin_manifest
        self._conn: sqlite3.Connection | None = None
        self._service_date: date | None = None
        self.discrepancy_log = _TableEventLog(self, DISCREPANCY_TABLE)
        self.ghost_log = _TableEventLog(self, GHOST_TABLE)
        self.gap_log = _TableEventLog(self, POLL_GAP_TABLE)
        self.completion_log = _TableEventLog(self, TRIP_COMPLETION_TABLE)
        self.delay_observation_log = _TableEventLog(self, DELAY_OBSERVATION_TABLE)

    @property
    def service_date(self) -> date | None:
        return self._service_date

    def partition_path(self, service_date: date) -> Path:
        return self._history_dir / f"{service_date.isoformat()}.db"

    def rotate(self, now: datetime) -> None:
        """Switch to the correct day's partition for `now`, opening/creating
        it if needed. A no-op if `now` still falls on the currently-open
        service_date (the common case: called once per poll cycle)."""
        service_date = service_date_for_instant(now)
        if service_date == self._service_date:
            return
        if self._conn is not None:
            self._conn.close()
        self._conn = self._open(service_date)
        self._service_date = service_date

    def _open(self, service_date: date) -> sqlite3.Connection:
        self._history_dir.mkdir(parents=True, exist_ok=True)
        path = self.partition_path(service_date)
        # Autocommit: write volume is occasional episodes, not per-cycle, so
        # per-statement commit overhead doesn't matter, and it means a crash
        # right after a `record()` call never loses an uncommitted row.
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute(_META_CREATE_SQL)
        for spec in _ALL_TABLES:
            conn.execute(spec.create_sql)

        digest = None
        if self._pin_manifest is not None:
            pin = self._pin_manifest.get(service_date)
            digest = pin.digest if pin is not None else None
        conn.execute(
            "INSERT OR IGNORE INTO meta (service_date, static_snapshot_digest, opened_at) "
            "VALUES (?, ?, ?)",
            (service_date.isoformat(), digest, datetime.now(timezone.utc).isoformat()),
        )
        return conn

    def _insert(self, spec: _TableSpec, row: tuple) -> None:
        if self._conn is None:
            raise RuntimeError("HistoryStore.rotate(now) must be called before recording events")
        self._conn.execute(spec.insert_sql, row)

    def read_completion_events(self, service_dates: Sequence[date]) -> CompletionEventsWindow:
        """Read `trip_completion_events` across multiple day-partitions at
        once -- unlike every other method on this class, this never touches
        `self._conn` (the live writer's currently-open partition). Each
        requested service_date's file is opened as its own independent
        READ-ONLY connection (`mode=ro` -- refuses to create a missing
        file, which is exactly the signal used to populate `days_missing`)
        and closed again immediately after.

        Safe against the live writer by construction for this call's only
        real caller (the weekly digest, firing Monday 8am over the
        previous Mon-Sun): every requested partition is from a fully
        elapsed service_date, already rotated past by the time this runs,
        so there is never a partition in the requested window still open
        for writes."""
        events: list[TripCompletionEvent] = []
        covered: list[date] = []
        missing: list[date] = []

        for service_date in service_dates:
            path = self.partition_path(service_date)
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            except sqlite3.OperationalError:
                missing.append(service_date)
                continue
            try:
                rows = conn.execute(
                    "SELECT trip_id, route_id, service_date, scheduled_terminus_arrival, "
                    "actual_terminus_arrival, delay_seconds, status FROM trip_completion_events"
                ).fetchall()
            finally:
                conn.close()
            covered.append(service_date)
            events.extend(_row_to_completion_event(row) for row in rows)

        return CompletionEventsWindow(
            events=tuple(events), days_covered=tuple(covered), days_missing=tuple(missing),
        )

    def counts(self) -> dict[str, int]:
        """Row counts per table for the currently-open partition (i.e.
        today's service_date so far) -- e.g. for a periodic burn-in summary
        log line. Empty dict if `rotate()` hasn't been called yet."""
        if self._conn is None:
            return {}
        return {
            spec.name: self._conn.execute(f"SELECT COUNT(*) FROM {spec.name}").fetchone()[0]
            for spec in _ALL_TABLES
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._service_date = None

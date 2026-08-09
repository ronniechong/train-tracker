"""Read one closed day-partition SQLite file (`history/store.py`'s
`{service_date}.db`) and compact its five event tables into pyarrow Tables
matching `schema.py`'s archival contract, ready for Parquet output.

Read-only by construction: opens the partition with SQLite's `mode=ro` URI
(refuses to create a missing file, matches `store.py.read_completion_events`'s
own pattern), never touches the live writer's connection -- this module is
the archiver's read side of the "read-only consumer of immutable artifacts"
architecture principle.

`service_date` is NOT re-derived per row from a timestamp -- the partition
filename already IS the service_date every row in this file belongs to
(that's what `HistoryStore.rotate()` guarantees), so it's applied uniformly
rather than recomputed from `recorded_at`/`observed_at` per row.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

from ..history.retention import partition_service_date
from .schema import GAP_MARKER_TABLES, SCHEMA_VERSION, TABLE_SCHEMAS

# SQL column lists, in schema order, excluding the internal `id` column --
# owned by this module rather than imported from `history/store.py`'s
# `_TableSpec`s, matching the "archiver only depends on the partition file
# contract" decoupling principle (it doesn't share code with the live
# write path, just the resulting SQL shape).
_SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "discrepancy_events": (
        "recorded_at", "trip_id", "observed_at", "discrepancy_type",
        "tu_value", "vp_value",
    ),
    "ghost_events": (
        "recorded_at", "trip_id", "last_seen_at", "last_seen_lat",
        "last_seen_lon", "reappeared_at", "reappear_lat", "reappear_lon",
        "loop_contained", "ghost_duration_s", "backoff_overlapped", "reason",
    ),
    "poll_gap_events": (
        "recorded_at", "started_at", "ended_at", "reason",
        "consecutive_failures", "max_level_reached_s",
    ),
    "trip_completion_events": (
        "recorded_at", "trip_id", "route_id", "scheduled_terminus_arrival",
        "actual_terminus_arrival", "delay_seconds", "status",
    ),
    "delay_observation_events": (
        "recorded_at", "trip_id", "route_id", "observed_at",
        "current_delay_s", "stops_remaining", "active_alert_flag",
    ),
}

# Columns whose SQLite value is a 0/1 INTEGER standing in for a bool.
_BOOL_COLUMNS: dict[str, frozenset[str]] = {
    "ghost_events": frozenset({"loop_contained", "backoff_overlapped"}),
    "delay_observation_events": frozenset({"active_alert_flag"}),
}

# Columns whose SQLite value is an ISO-8601 timestamp string.
_TIMESTAMP_COLUMNS: dict[str, frozenset[str]] = {
    "discrepancy_events": frozenset({"recorded_at", "observed_at"}),
    "ghost_events": frozenset(
        {"recorded_at", "last_seen_at", "reappeared_at"}
    ),
    "poll_gap_events": frozenset({"recorded_at", "started_at", "ended_at"}),
    "trip_completion_events": frozenset(
        {"recorded_at", "scheduled_terminus_arrival", "actual_terminus_arrival"}
    ),
    "delay_observation_events": frozenset({"recorded_at", "observed_at"}),
}


@dataclass(frozen=True)
class GapWindow:
    started_at: datetime
    ended_at: datetime
    reason: str


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _parse_row(table: str, columns: tuple[str, ...], raw_row: tuple) -> dict:
    bool_cols = _BOOL_COLUMNS.get(table, frozenset())
    ts_cols = _TIMESTAMP_COLUMNS.get(table, frozenset())
    row: dict = {}
    for column, value in zip(columns, raw_row):
        if value is not None and column in ts_cols:
            value = datetime.fromisoformat(value)
        elif value is not None and column in bool_cols:
            value = bool(value)
        row[column] = value
    return row


def _read_table(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Reads only the columns that actually exist in THIS partition's copy
    of `table`, filling any of `_SOURCE_COLUMNS[table]` that are missing
    with `None` -- a schema-evolution safeguard, not just a missing-table
    one. `archive/run.py` retries a closed day's compaction across runs
    until upload succeeds (the self-healing loop the 2026-08-08 empty-
    parquet incident fix relies on), so a partition written before a schema
    change (e.g. `ghost_events.reason`, added in v2) can still be compacted
    for the first time AFTER the code that reads it has moved on. Selecting
    the full `_SOURCE_COLUMNS` list unconditionally would raise
    `OperationalError: no such column` for that one column and, before this
    fix, silently drop the ENTIRE table's data for that day via the
    catch-all except below -- not just null out the one new column."""
    columns = _SOURCE_COLUMNS[table]
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
    if not existing:
        # Partition predates this table entirely (same honest "no data
        # here" signal as `store.py.read_completion_events` treats a
        # missing table as).
        return []
    available = tuple(c for c in columns if c in existing)
    missing = tuple(c for c in columns if c not in existing)
    cursor = conn.execute(f"SELECT {', '.join(available)} FROM {table}")  # noqa: S608
    rows = []
    for raw_row in cursor.fetchall():
        row = _parse_row(table, available, raw_row)
        for column in missing:
            row[column] = None
        rows.append(row)
    return rows


def _read_gap_windows(conn: sqlite3.Connection) -> list[GapWindow]:
    rows = _read_table(conn, "poll_gap_events")
    return [
        GapWindow(started_at=row["started_at"], ended_at=row["ended_at"], reason=row["reason"])
        for row in rows
    ]


def _base_archive_fields(service_date: date) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "service_date": service_date,
        "is_gap_marker": False,
        "gap_started_at": None,
        "gap_ended_at": None,
        "gap_reason": None,
    }


def _gap_marker_row(table: str, service_date: date, gap: GapWindow) -> dict:
    row = {column: None for column in _SOURCE_COLUMNS[table]}
    row.update(
        schema_version=SCHEMA_VERSION,
        service_date=service_date,
        is_gap_marker=True,
        gap_started_at=gap.started_at,
        gap_ended_at=gap.ended_at,
        gap_reason=gap.reason,
    )
    return row


def compact_partition(db_path: Path) -> dict[str, pa.Table]:
    """Read a closed `{service_date}.db` partition and return one pyarrow
    Table per `schema.py`'s five archived tables, gap markers included."""
    service_date = partition_service_date(db_path)
    conn = _open_readonly(db_path)
    try:
        gap_windows = _read_gap_windows(conn)
        tables: dict[str, pa.Table] = {}
        for name, schema in TABLE_SCHEMAS.items():
            rows = [
                {**_base_archive_fields(service_date), **row}
                for row in _read_table(conn, name)
            ]
            if name in GAP_MARKER_TABLES:
                rows.extend(
                    _gap_marker_row(name, service_date, gap) for gap in gap_windows
                )
            tables[name] = pa.Table.from_pylist(rows, schema=schema)
        return tables
    finally:
        conn.close()

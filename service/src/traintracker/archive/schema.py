"""The archival Parquet schema for each of `history/store.py`'s five event
tables — the versioned contract this milestone treats as its "archival
contract". Every column here traces to a deliberate decision; changing a
column here is a schema decision, not a refactor.

Every table gets the same four archive-derived columns appended (not
duplicated in each table's own column list below):
`schema_version`, `service_date`, `is_gap_marker`, `gap_started_at`,
`gap_ended_at`, `gap_reason`. The internal SQLite `id INTEGER PRIMARY KEY
AUTOINCREMENT` column is dropped everywhere -- verified nothing in
`store.py`'s own read methods select it back out, and it has no meaning
once rows are spread across many daily Parquet files.

Nullability note: every "real data" column in the four `GAP_MARKER_TABLES`
is nullable here even where the live SQLite table enforces NOT NULL for a
genuinely captured row (e.g. `trip_id`, `discrepancy_type`) -- a gap-marker
row nulls out every real data column by design, so the archived schema has
to allow that. SQLite's own constraint is unaffected; this is a
Parquet-only relaxation.
"""

from __future__ import annotations

import pyarrow as pa

# v2 (2026-08-09): added `ghost_events.reason` -- distinguishes why a ghost
# episode ended (reappeared/timed_out/flushed/completed/cancelled) instead
# of purely elapsed time. Rows archived before this ship date have
# `schema_version=1` and no `reason` (backfilling historical rows is out of
# scope, see milestone 11 -- gap-honesty over fabricated certainty).
SCHEMA_VERSION = 2

_UTC_TS = pa.timestamp("us", tz="UTC")

# Shared across every table -- appended, not part of each table's own list.
_ARCHIVE_COLUMNS = [
    pa.field("schema_version", pa.int32(), nullable=False),
    pa.field("service_date", pa.date32(), nullable=False),
    pa.field("is_gap_marker", pa.bool_(), nullable=False),
    pa.field("gap_started_at", _UTC_TS, nullable=True),
    pa.field("gap_ended_at", _UTC_TS, nullable=True),
    pa.field("gap_reason", pa.string(), nullable=True),
]


def _table_schema(*fields: pa.Field) -> pa.Schema:
    return pa.schema(list(fields) + _ARCHIVE_COLUMNS)


# `poll_gap_events` never carries gap-marker rows about itself (it IS the
# gap record), so its real-data columns keep their true NOT NULL shape.
DISCREPANCY_SCHEMA = _table_schema(
    pa.field("recorded_at", _UTC_TS, nullable=True),
    pa.field("trip_id", pa.string(), nullable=True),
    pa.field("observed_at", _UTC_TS, nullable=True),
    pa.field("discrepancy_type", pa.string(), nullable=True),
    pa.field("tu_value", pa.string(), nullable=True),
    pa.field("vp_value", pa.string(), nullable=True),
)

GHOST_SCHEMA = _table_schema(
    pa.field("recorded_at", _UTC_TS, nullable=True),
    pa.field("trip_id", pa.string(), nullable=True),
    pa.field("last_seen_at", _UTC_TS, nullable=True),
    pa.field("last_seen_lat", pa.float64(), nullable=True),
    pa.field("last_seen_lon", pa.float64(), nullable=True),
    pa.field("reappeared_at", _UTC_TS, nullable=True),
    pa.field("reappear_lat", pa.float64(), nullable=True),
    pa.field("reappear_lon", pa.float64(), nullable=True),
    pa.field("loop_contained", pa.bool_(), nullable=True),
    pa.field("ghost_duration_s", pa.float64(), nullable=True),
    pa.field("backoff_overlapped", pa.bool_(), nullable=True),
    # Nullable: gap-marker rows null it same as every other real-data
    # column, and rows compacted from a pre-v2 partition (no `reason`
    # column in that day's SQLite table) also carry it as null rather than
    # a fabricated value -- see `compact.py`'s per-column availability
    # check for how that's detected.
    pa.field("reason", pa.string(), nullable=True),
)

POLL_GAP_SCHEMA = _table_schema(
    pa.field("recorded_at", _UTC_TS, nullable=False),
    pa.field("started_at", _UTC_TS, nullable=False),
    pa.field("ended_at", _UTC_TS, nullable=False),
    pa.field("reason", pa.string(), nullable=False),
    pa.field("consecutive_failures", pa.int32(), nullable=False),
    pa.field("max_level_reached_s", pa.float64(), nullable=False),
)

TRIP_COMPLETION_SCHEMA = _table_schema(
    pa.field("recorded_at", _UTC_TS, nullable=True),
    pa.field("trip_id", pa.string(), nullable=True),
    pa.field("route_id", pa.string(), nullable=True),
    pa.field("scheduled_terminus_arrival", _UTC_TS, nullable=True),
    pa.field("actual_terminus_arrival", _UTC_TS, nullable=True),
    pa.field("delay_seconds", pa.int32(), nullable=True),
    pa.field("status", pa.string(), nullable=True),
)

DELAY_OBSERVATION_SCHEMA = _table_schema(
    pa.field("recorded_at", _UTC_TS, nullable=True),
    pa.field("trip_id", pa.string(), nullable=True),
    pa.field("route_id", pa.string(), nullable=True),
    pa.field("observed_at", _UTC_TS, nullable=True),
    pa.field("current_delay_s", pa.int32(), nullable=True),
    pa.field("stops_remaining", pa.int32(), nullable=True),
    pa.field("active_alert_flag", pa.bool_(), nullable=True),
)

# Table name (matches `history/store.py`'s SQLite table names -- also the
# Parquet layout's `data/<table>/...` directory segment) -> its schema.
TABLE_SCHEMAS: dict[str, pa.Schema] = {
    "discrepancy_events": DISCREPANCY_SCHEMA,
    "ghost_events": GHOST_SCHEMA,
    "poll_gap_events": POLL_GAP_SCHEMA,
    "trip_completion_events": TRIP_COMPLETION_SCHEMA,
    "delay_observation_events": DELAY_OBSERVATION_SCHEMA,
}

# Tables that receive synthetic gap-marker rows for overlapping
# `poll_gap_events` windows -- every table except `poll_gap_events` itself.
GAP_MARKER_TABLES: tuple[str, ...] = tuple(
    name for name in TABLE_SCHEMAS if name != "poll_gap_events"
)
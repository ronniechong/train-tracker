"""Persistence for M8 Insights precomputed rollups
(milestones/08-analytics-insights.md, "Compute strategy" + "Retention"
decisions, locked 2026-08-04).

Deliberately separate from `history/store.py`'s `HistoryStore`, same
reasoning as `digests/store.py`'s `WeeklyDigestStore`: this holds small
per-day, per-line aggregates (a handful of rows per service_date), not raw
events, so it gets INDEFINITE retention rather than the 60-day rolling cap
-- a "trends over time" dashboard that quietly loses history past 60 days
would undercut the whole point of the milestone. One connection, one file,
held for the process lifetime; no daily rotation.

The aggregation job (not yet built) is the only writer, and must run BEFORE
a service_date's `HistoryStore` partition ages out of the 60-day window --
this store cannot retroactively backfill a rollup for data that's already
been deleted (see milestone doc's architecture sketch).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .aggregate import DayRollup, DelayHistogramDayRollup, HourlyDayRollup, LineDayRollup

_CREATE_LINE_ROLLUPS_SQL = """
    CREATE TABLE IF NOT EXISTS insights_line_rollups (
        service_date TEXT NOT NULL,
        route_id TEXT NOT NULL,
        on_time_count INTEGER NOT NULL,
        late_count INTEGER NOT NULL,
        cancelled_count INTEGER NOT NULL,
        gap_count INTEGER NOT NULL,
        replacement_bus_count INTEGER NOT NULL,
        PRIMARY KEY (service_date, route_id)
    )
"""

_CREATE_HOURLY_ROLLUPS_SQL = """
    CREATE TABLE IF NOT EXISTS insights_hourly_rollups (
        service_date TEXT NOT NULL,
        route_id TEXT,
        hour_local INTEGER NOT NULL,
        completion_count INTEGER NOT NULL,
        PRIMARY KEY (service_date, route_id, hour_local)
    )
"""

# One row per service_date, recording when that day's rollup was last
# (re)computed -- separate table rather than a column on insights_line_
# rollups because it's one fact per DAY, not per line, and record_day's
# idempotent delete+reinsert of the line rows shouldn't need to duplicate
# it once per route_id.
#
# A staleness tooltip in the UI -- "data fresh as of HH:MM" -- needs
# this to exist BEFORE the API/frontend layer is built, not bolted on after.
# Only "today" is ever meaningfully stale (closed days are finalized once
# and never touched again -- there is no "staleness" concept for a day
# that's genuinely done), but every service_date still gets a real
# generated_at, for consistency and so a future UI decision isn't blocked
# on a schema gap.
_CREATE_ROLLUP_META_SQL = """
    CREATE TABLE IF NOT EXISTS insights_rollup_meta (
        service_date TEXT PRIMARY KEY,
        generated_at TEXT NOT NULL
    )
"""

# Chart 3 (fast-follow, built 2026-08-04). Network-wide, one row per
# service_date -- not per-line, matching the KPI row's own scope.
_CREATE_HISTOGRAM_ROLLUPS_SQL = """
    CREATE TABLE IF NOT EXISTS insights_histogram_rollups (
        service_date TEXT PRIMARY KEY,
        on_time_count INTEGER NOT NULL,
        late_5_10_count INTEGER NOT NULL,
        late_10_plus_count INTEGER NOT NULL,
        cancelled_count INTEGER NOT NULL,
        gap_count INTEGER NOT NULL
    )
"""


@dataclass(frozen=True)
class InsightsRangeQuery:
    """The result of summing rollups across a caller-supplied list of
    service_dates -- the store itself has no opinion on calendar-aligned
    vs. rolling ranges (locked: calendar-aligned, see milestone doc); that
    decision is made by whoever builds the `service_dates` list, not here.
    `days_covered` is the honesty signal for a partial calendar period
    (e.g. "Last 7 days" picked on a Tuesday) -- same pattern as
    `CompletionEventsWindow.days_covered`."""

    days_covered: tuple[date, ...]
    line_rollups: tuple[LineDayRollup, ...]  # one row per route_id, summed across days_covered
    hourly_rollups: tuple[HourlyDayRollup, ...]  # one row per (route_id, hour_local), summed
    # UNSUMMED per-day rows, one entry per days_covered -- added after the
    # frontend build found the summed-only `line_rollups` above can't back
    # a chart that needs a point per day (cancellations/delays over time)
    # or a per-day-of-week split (weekday vs. weekend). `line_rollups`
    # stays as the cheap common case for charts that only need range
    # totals; this is for the two that don't.
    daily_line_rollups: dict[date, tuple[LineDayRollup, ...]]
    # Per-date generated_at, NOT a single min/max across the range --
    # deliberately not collapsed to one number. A closed day's generated_at
    # is a fixed historical fact ("finalized at X"), not staleness; only a
    # date still being periodically refreshed (in practice, only "today")
    # has a genuine freshness concept. Collapsing to the oldest timestamp
    # in the range would misreport a mostly-fresh range as stale just
    # because one long-settled day happened to finalize a while ago;
    # collapsing to the newest would hide that an older day might be
    # missing this field entirely. The caller (API/frontend) looks up
    # whichever date it actually cares about -- typically today's, for
    # the staleness tooltip.
    generated_at_by_date: dict[date, datetime]
    # Chart 3 -- summed across days_covered, same shape/reasoning as
    # line_rollups above (network-wide, so no per-line split needed).
    histogram_rollup: DelayHistogramDayRollup


class InsightsStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit, same reasoning as WeeklyDigestStore/HistoryStore:
        # write volume here is at most once per service_date (plus
        # periodic same-day refreshes for "today"), so per-statement
        # commit overhead is irrelevant, and a crash mid-write can never
        # leave an uncommitted row silently lost.
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute(_CREATE_LINE_ROLLUPS_SQL)
        self._conn.execute(_CREATE_HOURLY_ROLLUPS_SQL)
        self._conn.execute(_CREATE_ROLLUP_META_SQL)
        self._conn.execute(_CREATE_HISTOGRAM_ROLLUPS_SQL)

    def record_day(self, rollup: DayRollup) -> None:
        """Replaces any existing rows for this service_date -- makes the
        write idempotent by construction, which matters for "today":
        refreshing a still-accumulating day's rollup means re-running this
        with a superset of the previous call's events, not appending
        duplicates. `generated_at` is stamped fresh on every call (not
        preserved from a prior write for the same date), since a re-run IS
        the definition of "just regenerated" for that service_date."""
        service_date_iso = rollup.service_date.isoformat()
        self._conn.execute(
            "DELETE FROM insights_line_rollups WHERE service_date = ?", (service_date_iso,)
        )
        self._conn.execute(
            "DELETE FROM insights_hourly_rollups WHERE service_date = ?", (service_date_iso,)
        )
        self._conn.execute(
            "DELETE FROM insights_histogram_rollups WHERE service_date = ?", (service_date_iso,)
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO insights_rollup_meta (service_date, generated_at) "
            "VALUES (?, ?)",
            (service_date_iso, datetime.now(timezone.utc).isoformat()),
        )
        for line in rollup.line_rollups:
            self._conn.execute(
                """
                INSERT INTO insights_line_rollups
                    (service_date, route_id, on_time_count, late_count,
                     cancelled_count, gap_count, replacement_bus_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_date_iso,
                    line.route_id,
                    line.on_time_count,
                    line.late_count,
                    line.cancelled_count,
                    line.gap_count,
                    line.replacement_bus_count,
                ),
            )
        for hourly in rollup.hourly_rollups:
            self._conn.execute(
                """
                INSERT INTO insights_hourly_rollups
                    (service_date, route_id, hour_local, completion_count)
                VALUES (?, ?, ?, ?)
                """,
                (service_date_iso, hourly.route_id, hourly.hour_local, hourly.completion_count),
            )
        h = rollup.histogram_rollup
        self._conn.execute(
            """
            INSERT INTO insights_histogram_rollups
                (service_date, on_time_count, late_5_10_count, late_10_plus_count,
                 cancelled_count, gap_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (service_date_iso, h.on_time_count, h.late_5_10_count, h.late_10_plus_count, h.cancelled_count, h.gap_count),
        )

    def read_range(self, service_dates: tuple[date, ...]) -> InsightsRangeQuery:
        """Sums rollups across whichever service_dates the caller asks for
        -- a calendar week, a calendar month, "today" alone, or any other
        range; the range-shape decision lives entirely with the caller.
        `days_covered` only includes dates that actually have a persisted
        rollup (the aggregation job has run for them), same distinction as
        `CompletionEventsWindow.days_missing` -- a date with no rollup yet
        is an unknown, not a genuine zero."""
        if not service_dates:
            return InsightsRangeQuery(
                days_covered=(),
                line_rollups=(),
                hourly_rollups=(),
                generated_at_by_date={},
                daily_line_rollups={},
                histogram_rollup=DelayHistogramDayRollup(0, 0, 0, 0, 0),
            )

        placeholders = ",".join("?" for _ in service_dates)
        iso_dates = [d.isoformat() for d in service_dates]

        covered_rows = self._conn.execute(
            f"SELECT DISTINCT service_date FROM insights_line_rollups "
            f"WHERE service_date IN ({placeholders})",
            iso_dates,
        ).fetchall()
        days_covered = tuple(sorted(date.fromisoformat(r[0]) for r in covered_rows))

        meta_rows = self._conn.execute(
            f"SELECT service_date, generated_at FROM insights_rollup_meta "
            f"WHERE service_date IN ({placeholders})",
            iso_dates,
        ).fetchall()
        generated_at_by_date = {
            date.fromisoformat(row[0]): datetime.fromisoformat(row[1]) for row in meta_rows
        }

        line_rows = self._conn.execute(
            f"""
            SELECT route_id, SUM(on_time_count), SUM(late_count),
                   SUM(cancelled_count), SUM(gap_count), SUM(replacement_bus_count)
            FROM insights_line_rollups
            WHERE service_date IN ({placeholders})
            GROUP BY route_id
            ORDER BY route_id
            """,
            iso_dates,
        ).fetchall()
        line_rollups = tuple(
            LineDayRollup(
                route_id=row[0],
                on_time_count=row[1],
                late_count=row[2],
                cancelled_count=row[3],
                gap_count=row[4],
                replacement_bus_count=row[5],
            )
            for row in line_rows
        )

        hourly_rows = self._conn.execute(
            f"""
            SELECT route_id, hour_local, SUM(completion_count)
            FROM insights_hourly_rollups
            WHERE service_date IN ({placeholders})
            GROUP BY route_id, hour_local
            ORDER BY route_id, hour_local
            """,
            iso_dates,
        ).fetchall()
        hourly_rollups = tuple(
            HourlyDayRollup(route_id=row[0], hour_local=row[1], completion_count=row[2])
            for row in hourly_rows
        )

        daily_rows = self._conn.execute(
            f"""
            SELECT service_date, route_id, on_time_count, late_count,
                   cancelled_count, gap_count, replacement_bus_count
            FROM insights_line_rollups
            WHERE service_date IN ({placeholders})
            ORDER BY service_date, route_id
            """,
            iso_dates,
        ).fetchall()
        daily_line_rollups: dict[date, list[LineDayRollup]] = {}
        for row in daily_rows:
            day = date.fromisoformat(row[0])
            daily_line_rollups.setdefault(day, []).append(
                LineDayRollup(
                    route_id=row[1],
                    on_time_count=row[2],
                    late_count=row[3],
                    cancelled_count=row[4],
                    gap_count=row[5],
                    replacement_bus_count=row[6],
                )
            )

        histogram_row = self._conn.execute(
            f"""
            SELECT SUM(on_time_count), SUM(late_5_10_count), SUM(late_10_plus_count),
                   SUM(cancelled_count), SUM(gap_count)
            FROM insights_histogram_rollups
            WHERE service_date IN ({placeholders})
            """,
            iso_dates,
        ).fetchone()
        histogram_rollup = DelayHistogramDayRollup(
            on_time_count=histogram_row[0] or 0,
            late_5_10_count=histogram_row[1] or 0,
            late_10_plus_count=histogram_row[2] or 0,
            cancelled_count=histogram_row[3] or 0,
            gap_count=histogram_row[4] or 0,
        )

        return InsightsRangeQuery(
            days_covered=days_covered,
            line_rollups=line_rollups,
            hourly_rollups=hourly_rollups,
            generated_at_by_date=generated_at_by_date,
            daily_line_rollups={day: tuple(rows) for day, rows in daily_line_rollups.items()},
            histogram_rollup=histogram_rollup,
        )

    def close(self) -> None:
        self._conn.close()

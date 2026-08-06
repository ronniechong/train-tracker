"""trip_id join-rate computation: realtime feed entities against a pinned
static snapshot's trip_ids.

Pure function, no I/O — the realtime trip_id stream comes from the state
store and metric emission from Prometheus; this module only computes the
ratio.

The primary ratio's denominator counts only `SCHEDULED`-relationship
entities: `ADDED`/`DUPLICATED`/`UNSCHEDULED` trip_ids are real-time-only by
the GTFS-RT spec and will never appear in the static timetable, so they
would unfairly depress the ratio if included.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEDULED = "SCHEDULED"


@dataclass(frozen=True)
class RealtimeTripRef:
    trip_id: str
    schedule_relationship: str  # SCHEDULED, ADDED, CANCELED, DUPLICATED, UNSCHEDULED


@dataclass(frozen=True)
class JoinRateResult:
    total_scheduled: int
    matched_scheduled: int
    join_pct_scheduled_only: float
    total_all: int
    matched_all: int
    join_pct_all: float
    unmatched_scheduled_trip_ids: frozenset[str]


def compute_join_rate(
    realtime_trips: list[RealtimeTripRef],
    snapshot_trip_ids: frozenset[str],
) -> JoinRateResult:
    total_all = len(realtime_trips)
    matched_all = sum(1 for t in realtime_trips if t.trip_id in snapshot_trip_ids)

    scheduled = [t for t in realtime_trips if t.schedule_relationship == SCHEDULED]
    unmatched_scheduled = frozenset(
        t.trip_id for t in scheduled if t.trip_id not in snapshot_trip_ids
    )
    total_scheduled = len(scheduled)
    matched_scheduled = total_scheduled - len(unmatched_scheduled)

    def pct(matched: int, total: int) -> float:
        return (matched / total * 100.0) if total else 100.0

    return JoinRateResult(
        total_scheduled=total_scheduled,
        matched_scheduled=matched_scheduled,
        join_pct_scheduled_only=pct(matched_scheduled, total_scheduled),
        total_all=total_all,
        matched_all=matched_all,
        join_pct_all=pct(matched_all, total_all),
        unmatched_scheduled_trip_ids=unmatched_scheduled,
    )

"""M8 Insights: per-day rollup aggregation (milestones/08-analytics-insights.md).

Pure computation, no I/O, mirroring `ai/weekly_digest.py`'s split between
computation and persistence -- `aggregate_day` below takes one service_date's
already-read `TripCompletionEvent`s and produces what `InsightsStore` persists.

`-R` replacement-bus route_ids (`gtfs/routes.py`) are corrected against
PTV's own published reliability methodology: a cancelled train counts against reliability
regardless of whether a substitute bus covered the corridor, so `-R`
completions are NEVER merged into the parent line's on-time/volume counts --
merging would double-count an already-`cancelled` scheduled trip as if it
had been delivered. `-R` volume is tracked separately, per line, purely as
the reason source for a zero/low-completion row ("ran as replacement
buses").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from ..gtfs.gtfstime import MELBOURNE_TZ
from ..gtfs.routes import REPLACEMENT_BUS_SHORT_NAME, Route, replacement_bus_route_id
from ..state.completion import TripCompletionEvent

_MELBOURNE_ZONE = ZoneInfo(MELBOURNE_TZ)


@dataclass(frozen=True)
class LineDayRollup:
    """One real line's (never a `-R` row) counts for a single service_date."""

    route_id: str
    on_time_count: int
    late_count: int
    cancelled_count: int
    gap_count: int
    replacement_bus_count: int  # that line's own -R route_id's completions, same day


@dataclass(frozen=True)
class HourlyDayRollup:
    """Completions at terminus, bucketed by Melbourne-local hour of arrival
    (chart 4) -- NOT a departure-frequency count, see milestone doc's
    honesty note on this chart's framing. `route_id=None` means network-wide
    (summed across all real lines), used for the "All lines" view."""

    route_id: str | None
    hour_local: int  # 0-23, Australia/Melbourne
    completion_count: int


# Chart 3 (on-time performance histogram). Bucket boundaries deliberately
# do NOT match the milestone doc's original "1-5min/5-10min/10+min" sketch
# -- that would double-count against the already-locked on-time threshold
# (<=4:59, ON_TIME_THRESHOLD_S in state/completion.py), since a delay of
# 1-4:59 is already scored on_time. Buckets here start where "late"
# actually starts, avoiding that overlap. Found and fixed 2026-08-04 while
# finally building this chart (was deferred at design-review time).
LATE_10_MIN_THRESHOLD_S = 600


@dataclass(frozen=True)
class DelayHistogramDayRollup:
    """Network-wide (not per-line, matching the KPI row's own scope) delay
    distribution for one service_date. `gap_count` included for the same
    honesty reason `undetermined_gap` is always its own segment elsewhere
    in this codebase -- never silently folded into another bucket."""

    on_time_count: int
    late_5_10_count: int
    late_10_plus_count: int
    cancelled_count: int
    gap_count: int


@dataclass(frozen=True)
class DayRollup:
    service_date: date
    line_rollups: tuple[LineDayRollup, ...]
    hourly_rollups: tuple[HourlyDayRollup, ...]
    histogram_rollup: DelayHistogramDayRollup


def _is_replacement_bus(route_id: str, routes_by_id: dict[str, Route]) -> bool:
    route = routes_by_id.get(route_id)
    return route is not None and route.short_name == REPLACEMENT_BUS_SHORT_NAME


def aggregate_day(
    service_date: date,
    events: tuple[TripCompletionEvent, ...],
    routes_by_id: dict[str, Route],
) -> DayRollup:
    """`routes_by_id` is the static-snapshot route lookup (`gtfs/routes.py`'s
    `parse_routes` output) pinned for this service_date -- needed to tell a
    real line's route_id apart from its paired `-R` id, since that
    distinction lives in `route_short_name`, not the id string alone."""

    # route_id -> [on_time, late, cancelled, gap]
    by_line: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    status_index = {"on_time": 0, "late": 1, "cancelled": 2, "undetermined_gap": 3}
    # hour_local -> count, per route_id (real lines only -- an -R event
    # contributes to nothing here; this chart is about real-line service)
    hourly: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    histogram = {"on_time": 0, "late_5_10": 0, "late_10_plus": 0, "cancelled": 0, "gap": 0}

    for event in events:
        if event.route_id is None:
            continue
        idx = status_index.get(event.status)
        if idx is not None:
            by_line[event.route_id][idx] += 1
        if event.actual_terminus_arrival is not None and not _is_replacement_bus(
            event.route_id, routes_by_id
        ):
            local_hour = event.actual_terminus_arrival.astimezone(_MELBOURNE_ZONE).hour
            hourly[event.route_id][local_hour] += 1

        if _is_replacement_bus(event.route_id, routes_by_id):
            continue  # network-wide histogram is about real-line service, same as hourly above
        if event.status == "on_time":
            histogram["on_time"] += 1
        elif event.status == "late":
            delay = event.delay_seconds or 0
            if delay >= LATE_10_MIN_THRESHOLD_S:
                histogram["late_10_plus"] += 1
            else:
                histogram["late_5_10"] += 1
        elif event.status == "cancelled":
            histogram["cancelled"] += 1
        elif event.status == "undetermined_gap":
            histogram["gap"] += 1

    real_line_ids = {
        route_id
        for route_id in by_line
        if not _is_replacement_bus(route_id, routes_by_id)
    }

    line_rollups = tuple(
        LineDayRollup(
            route_id=route_id,
            on_time_count=by_line[route_id][0],
            late_count=by_line[route_id][1],
            cancelled_count=by_line[route_id][2],
            gap_count=by_line[route_id][3],
            # All 4 statuses summed -- this is a volume/reason signal ("N
            # bus trips recorded that day"), not itself scored for on-time
            # performance, so on_time/late/cancelled/gap all count equally.
            replacement_bus_count=sum(by_line[replacement_bus_route_id(route_id)]),
        )
        for route_id in sorted(real_line_ids)
    )

    hourly_rollups: list[HourlyDayRollup] = []
    network_wide: dict[int, int] = defaultdict(int)
    for route_id, hours in hourly.items():
        for hour_local, count in hours.items():
            hourly_rollups.append(
                HourlyDayRollup(route_id=route_id, hour_local=hour_local, completion_count=count)
            )
            network_wide[hour_local] += count
    for hour_local, count in network_wide.items():
        hourly_rollups.append(
            HourlyDayRollup(route_id=None, hour_local=hour_local, completion_count=count)
        )

    return DayRollup(
        service_date=service_date,
        line_rollups=line_rollups,
        hourly_rollups=tuple(sorted(hourly_rollups, key=lambda r: (r.route_id or "", r.hour_local))),
        histogram_rollup=DelayHistogramDayRollup(
            on_time_count=histogram["on_time"],
            late_5_10_count=histogram["late_5_10"],
            late_10_plus_count=histogram["late_10_plus"],
            cancelled_count=histogram["cancelled"],
            gap_count=histogram["gap"],
        ),
    )

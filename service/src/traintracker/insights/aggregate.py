"""M8 Insights: per-day rollup aggregation (milestones/08-analytics-insights.md).

Pure computation, no I/O, mirroring `ai/weekly_digest.py`'s split between
computation and persistence -- `aggregate_day` below takes one service_date's
already-read `TripCompletionEvent`s and produces what `InsightsStore` persists.

`-R` replacement-bus route_ids (`gtfs/routes.py`) are corrected against
PTV's own published reliability methodology (2026-08-04 design review,
work-docs milestone doc): a cancelled train counts against reliability
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


@dataclass(frozen=True)
class DayRollup:
    service_date: date
    line_rollups: tuple[LineDayRollup, ...]
    hourly_rollups: tuple[HourlyDayRollup, ...]


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
    )

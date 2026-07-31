"""Query logic for "what's the next train at this station" -- pure
functions only, no I/O (same testability pattern as `state/merge.py`,
`state/station.py`, `gtfs/joinrate.py`). Loading/caching the underlying
static snapshot is `schedule_cache.py`'s job; overlaying live Trip Updates
predictions on top of what this module returns is the API route's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .gtfstime import gtfs_time_to_utc
from .snapshot import TripRecord
from .stop_times import StopTimeRecord
from .stops import Stop


@dataclass(frozen=True)
class ScheduledDeparture:
    trip_id: str
    route_id: str
    direction_id: int | None
    headsign: str
    scheduled_time: datetime
    # The exact platform stop_id this scheduled time came from -- kept
    # (not just the station_id) so a live Trip Updates overlay can match
    # the right stop_time_update entry precisely, not just "any platform
    # belonging to this station" (interchange stations can have several).
    stop_id: str


def platforms_for_station(stops: dict[str, Stop], station_id: str) -> frozenset[str]:
    """Every platform stop_id grouped under `station_id` via
    `parent_station`. Falls back to `station_id` itself only when it has no
    grouped children -- some stations have no separate parent/platform
    split in the data, and stop_times.txt keys directly off the station's
    own stop_id in that case."""
    platforms = {sid for sid, stop in stops.items() if stop.parent_station == station_id}
    if not platforms and station_id in stops:
        platforms = {station_id}
    return frozenset(platforms)


def next_departures(
    trips: list[TripRecord],
    stop_times: list[StopTimeRecord],
    platform_ids: frozenset[str],
    service_date: date,
    after: datetime,
    limit_per_direction: int = 3,
) -> list[ScheduledDeparture]:
    """`trips` must already be scoped to trips active on `service_date`
    (e.g. via `StaticSnapshot.trip_ids_for_service_date` +
    `StaticSnapshot.trips`) -- this function has no calendar and does not
    filter by service_id itself, only by time and platform."""
    trips_by_id = {t.trip_id: t for t in trips}

    candidates: list[ScheduledDeparture] = []
    for st in stop_times:
        if st.stop_id not in platform_ids:
            continue
        trip = trips_by_id.get(st.trip_id)
        if trip is None:
            continue
        time_str = st.departure_time or st.arrival_time
        if not time_str:
            continue
        scheduled_time = gtfs_time_to_utc(service_date, time_str)
        if scheduled_time <= after:
            continue
        candidates.append(
            ScheduledDeparture(
                trip_id=trip.trip_id,
                route_id=trip.route_id,
                direction_id=trip.direction_id,
                headsign=trip.trip_headsign,
                scheduled_time=scheduled_time,
                stop_id=st.stop_id,
            )
        )

    by_direction: dict[int | None, list[ScheduledDeparture]] = {}
    for dep in candidates:
        by_direction.setdefault(dep.direction_id, []).append(dep)

    limited: list[ScheduledDeparture] = []
    for deps in by_direction.values():
        deps.sort(key=lambda d: d.scheduled_time)
        limited.extend(deps[:limit_per_direction])

    limited.sort(key=lambda d: d.scheduled_time)
    return limited

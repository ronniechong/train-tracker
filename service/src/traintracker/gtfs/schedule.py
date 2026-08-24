"""Query logic for "what's the next train at this station" -- pure
functions only, no I/O (same testability pattern as `state/merge.py`,
`state/station.py`, `gtfs/joinrate.py`). Loading/caching the underlying
static snapshot is `schedule_cache.py`'s job; overlaying live Trip Updates
predictions on top of what this module returns is the API route's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..state.merge import TrainSnapshot
from .gtfstime import gtfs_time_to_utc
from .snapshot import TripRecord
from .stop_times import StopTimeRecord
from .stops import Stop


@dataclass(frozen=True)
class NextServiceLeg:
    """One same-line leg of a next-service lookup (M13) -- a same-trip
    departure/arrival pair, unlike `ScheduledDeparture` which only carries
    a single stop/time (that shape is "what departs here next", this one
    is "does this specific trip also reach my destination, and when")."""

    trip_id: str
    route_id: str
    headsign: str
    departure_time: datetime
    from_stop_id: str
    arrival_time: datetime
    to_stop_id: str


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


def added_departures(
    snapshots: dict[str, TrainSnapshot],
    stops: dict[str, Stop],
    platform_ids: frozenset[str],
    after: datetime,
    limit_per_direction: int = 3,
) -> list[ScheduledDeparture]:
    """Real-time-only extra services (TU `schedule_relationship` ADDED)
    calling at one of `platform_ids` sometime after `after` -- these have
    no static `stop_times.txt` row, so `next_departures()` above never sees
    them at all. `headsign` is derived from the trip's final
    `stop_time_update`'s stop name (a projection of where it's headed, not
    a schedule fact -- an ADDED trip's TU trip descriptor carries no
    headsign of its own); `direction_id` is left `None` for the same
    reason, so results aren't grouped by direction like `next_departures`
    does -- just capped flat after sorting by time.
    """
    candidates: list[ScheduledDeparture] = []
    for trip_id, snapshot in snapshots.items():
        if snapshot.schedule_relationship != "ADDED" or snapshot.route_id is None:
            continue
        match = next(
            (s for s in snapshot.stop_time_updates if s.stop_id in platform_ids), None
        )
        if match is None:
            continue
        time_epoch = match.departure_time if match.departure_time is not None else match.arrival_time
        if time_epoch is None:
            continue
        scheduled_time = datetime.fromtimestamp(int(time_epoch), tz=timezone.utc)
        if scheduled_time <= after:
            continue
        final_stop_id = snapshot.stop_time_updates[-1].stop_id
        final_stop = stops.get(final_stop_id) if final_stop_id else None
        headsign = final_stop.name if final_stop is not None else (final_stop_id or "Extra service")
        candidates.append(
            ScheduledDeparture(
                trip_id=trip_id,
                route_id=snapshot.route_id,
                direction_id=None,
                headsign=headsign,
                scheduled_time=scheduled_time,
                stop_id=match.stop_id,
            )
        )

    candidates.sort(key=lambda d: d.scheduled_time)
    return candidates[:limit_per_direction]


def next_service_same_line(
    trips: list[TripRecord],
    stop_times: list[StopTimeRecord],
    from_platform_ids: frozenset[str],
    to_platform_ids: frozenset[str],
    service_date: date,
    after: datetime,
) -> NextServiceLeg | None:
    """Soonest single trip that departs `from_platform_ids` after `after`
    AND later calls at one of `to_platform_ids` on the SAME trip (a real
    same-line service, not just "these two stations happen to share a
    line" -- direction and stopping pattern are what actually decide
    reachability, and only a shared trip_id proves both).

    `trips` must already be scoped to `service_date` (same convention as
    `next_departures`, no calendar filtering here). Returns `None` when no
    such trip exists today after `after` -- ambiguous with "no more
    services today" vs. "this line never connects these two stations";
    the caller (M13's `find_next_service`) distinguishes those via
    `lines_no_service_today`/a same-line existence check, not this
    function.
    """
    trips_by_id = {t.trip_id: t for t in trips}
    by_trip: dict[str, list[StopTimeRecord]] = {}
    for st in stop_times:
        if st.trip_id in trips_by_id:
            by_trip.setdefault(st.trip_id, []).append(st)

    best: NextServiceLeg | None = None
    for trip_id, records in by_trip.items():
        trip = trips_by_id[trip_id]
        records.sort(key=lambda r: r.stop_sequence)
        from_record = next((r for r in records if r.stop_id in from_platform_ids), None)
        if from_record is None:
            continue
        departure_str = from_record.departure_time or from_record.arrival_time
        if not departure_str:
            continue
        departure_time = gtfs_time_to_utc(service_date, departure_str)
        if departure_time <= after:
            continue
        to_record = next(
            (
                r
                for r in records
                if r.stop_id in to_platform_ids and r.stop_sequence > from_record.stop_sequence
            ),
            None,
        )
        if to_record is None:
            continue
        arrival_str = to_record.arrival_time or to_record.departure_time
        if not arrival_str:
            continue
        arrival_time = gtfs_time_to_utc(service_date, arrival_str)
        if best is None or departure_time < best.departure_time:
            best = NextServiceLeg(
                trip_id=trip_id,
                route_id=trip.route_id,
                headsign=trip.trip_headsign,
                departure_time=departure_time,
                from_stop_id=from_record.stop_id,
                arrival_time=arrival_time,
                to_stop_id=to_record.stop_id,
            )
    return best

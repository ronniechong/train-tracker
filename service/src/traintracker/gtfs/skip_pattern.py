"""Derives a "skipped N stops" count per trip by comparing its static stop
pattern against the most common pattern among trips sharing the same
route+direction+span (M12 #6). No GTFS field states this directly -- neither
`trips.txt` nor a `trip_short_name` column exists in the real feed -- and
Metro's own passenger-facing "express"/"limited express" labelling is
inconsistently applied in practice, so this counts stops only, it never
attempts to reproduce Metro's own category names.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .routes import REPLACEMENT_BUS_SHORT_NAME, Route
from .snapshot import TripRecord
from .stop_times import StopTimeRecord
from .stops import Stop

# Parent-station codes for the three City Loop-only stations (Flagstaff,
# Melbourne Central, Parliament). Flinders Street and Southern Cross are
# both non-Loop city stations served by every trip regardless of routing,
# so they don't distinguish via-Loop from direct-to-city trips.
CITY_LOOP_PARENT_STATIONS = frozenset(
    {"vic:rail:FGS", "vic:rail:MCE", "vic:rail:PAR"}
)


def _via_city_loop(stop_ids: tuple[str, ...], stops: dict[str, Stop]) -> bool:
    for stop_id in stop_ids:
        stop = stops.get(stop_id)
        if stop is not None and stop.parent_station in CITY_LOOP_PARENT_STATIONS:
            return True
    return False


def compute_skip_stop_counts(
    trips: list[TripRecord],
    stop_times: list[StopTimeRecord],
    stops: dict[str, Stop],
    routes: dict[str, Route],
) -> dict[str, int | None]:
    """trip_id -> number of stops it skips relative to the most common
    pattern among comparable trips, or `None` when there's no comparable
    group to judge it against (fewer than 2 trips share its route,
    direction, via-Loop-ness, and start/end stops -- an honest "unknown",
    not a guess).

    Bus-replacement routes (`REPLACEMENT_BUS_SHORT_NAME`) are excluded
    entirely -- their stop_times geometry isn't a train "skip" pattern.
    """
    ordered_by_trip: dict[str, list[StopTimeRecord]] = defaultdict(list)
    for record in stop_times:
        ordered_by_trip[record.trip_id].append(record)

    route_by_trip = {t.trip_id: t.route_id for t in trips}
    direction_by_trip = {t.trip_id: t.direction_id for t in trips}

    stop_ids_by_trip: dict[str, tuple[str, ...]] = {}
    group_key_by_trip: dict[str, tuple[str, int | None, bool, str, str]] = {}
    for trip_id, records in ordered_by_trip.items():
        route_id = route_by_trip.get(trip_id)
        if route_id is None:
            continue
        route = routes.get(route_id)
        if route is not None and route.short_name == REPLACEMENT_BUS_SHORT_NAME:
            continue
        records.sort(key=lambda r: r.stop_sequence)
        stop_ids = tuple(r.stop_id for r in records)
        if not stop_ids:
            continue
        stop_ids_by_trip[trip_id] = stop_ids
        group_key_by_trip[trip_id] = (
            route_id,
            direction_by_trip.get(trip_id),
            _via_city_loop(stop_ids, stops),
            stop_ids[0],
            stop_ids[-1],
        )

    trip_ids_by_group: dict[tuple[str, int | None, bool, str, str], list[str]] = defaultdict(list)
    for trip_id, key in group_key_by_trip.items():
        trip_ids_by_group[key].append(trip_id)

    counts: dict[str, int | None] = {}
    for key, trip_ids in trip_ids_by_group.items():
        if len(trip_ids) < 2:
            for trip_id in trip_ids:
                counts[trip_id] = None
            continue
        pattern_votes = Counter(stop_ids_by_trip[trip_id] for trip_id in trip_ids)
        normal_pattern = frozenset(pattern_votes.most_common(1)[0][0])
        for trip_id in trip_ids:
            this_pattern = frozenset(stop_ids_by_trip[trip_id])
            counts[trip_id] = len(normal_pattern - this_pattern)
    return counts

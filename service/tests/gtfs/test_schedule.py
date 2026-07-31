from datetime import date, datetime, timezone

from traintracker.gtfs.gtfstime import gtfs_time_to_utc
from traintracker.gtfs.schedule import next_departures, platforms_for_station
from traintracker.gtfs.stops import Stop


def test_platforms_for_station_groups_by_parent_station(sample_stops):
    assert platforms_for_station(sample_stops, "STATION_A") == frozenset({"PLAT_A1"})
    assert platforms_for_station(sample_stops, "STATION_B") == frozenset({"PLAT_B1"})


def test_platforms_for_station_falls_back_to_direct_stop_id():
    stops = {"DIRECT": Stop(stop_id="DIRECT", name="Direct Stop", latitude=0.0, longitude=0.0)}
    assert platforms_for_station(stops, "DIRECT") == frozenset({"DIRECT"})


def test_platforms_for_station_unknown_returns_empty(sample_stops):
    assert platforms_for_station(sample_stops, "NOT_A_REAL_STATION") == frozenset()


def _active_trips(sample_snapshot, service_date):
    active_ids = sample_snapshot.trip_ids_for_service_date(service_date)
    return [t for t in sample_snapshot.trips if t.trip_id in active_ids]


def test_next_departures_orders_by_time_and_filters_past(sample_snapshot, sample_stop_times):
    weekday = date(2026, 7, 20)  # Monday
    trips = _active_trips(sample_snapshot, weekday)
    platform_ids = frozenset({"PLAT_A1"})
    # PLAT_A1 sees WEEKDAY_TRIP_1's 08:00 departure and WEEKDAY_TRIP_2's
    # 08:15 arrival (its final stop). Setting `after` to 08:05 (via the
    # same gtfs_time_to_utc conversion next_departures itself uses, so this
    # doesn't depend on hand-computing the Melbourne/UTC offset) should
    # drop the 08:00 departure and keep only the 08:15 one.
    after = gtfs_time_to_utc(weekday, "08:05:00")

    deps = next_departures(trips, sample_stop_times, platform_ids, weekday, after)
    assert all(d.scheduled_time > after for d in deps)
    assert {d.trip_id for d in deps} == {"WEEKDAY_TRIP_2"}
    times = [d.scheduled_time for d in deps]
    assert times == sorted(times)


def test_next_departures_respects_limit_per_direction(sample_snapshot, sample_stop_times):
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    platform_ids = frozenset({"PLAT_A1", "PLAT_B1"})
    after = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    deps = next_departures(trips, sample_stop_times, platform_ids, weekday, after, limit_per_direction=1)
    by_direction: dict[int | None, int] = {}
    for d in deps:
        by_direction[d.direction_id] = by_direction.get(d.direction_id, 0) + 1
    assert all(count <= 1 for count in by_direction.values())


def test_next_departures_falls_back_to_arrival_time_when_departure_blank(
    sample_snapshot, sample_stop_times
):
    # WEEKDAY_TRIP_1's second stop (PLAT_B1) has a blank departure_time in
    # the fixture -- next_departures must fall back to arrival_time rather
    # than skipping the row entirely.
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    platform_ids = frozenset({"PLAT_B1"})
    after = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    deps = next_departures(trips, sample_stop_times, platform_ids, weekday, after)
    trip_ids = {d.trip_id for d in deps}
    assert "WEEKDAY_TRIP_1" in trip_ids


def test_next_departures_empty_when_no_more_services(sample_snapshot, sample_stop_times):
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    platform_ids = frozenset({"PLAT_A1", "PLAT_B1"})
    # Well after every scheduled time in the fixture (all times are ~08-09am).
    after = datetime(2026, 7, 20, 23, 0, tzinfo=timezone.utc)

    deps = next_departures(trips, sample_stop_times, platform_ids, weekday, after)
    assert deps == []


def test_next_departures_excludes_inactive_service(sample_snapshot, sample_stop_times):
    # WEEKEND trips are not active on a Monday -- passing only the
    # WEEKDAY-active trips must not surface WEEKEND_TRIP_* departures at all.
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    platform_ids = frozenset({"PLAT_A1", "PLAT_B1"})
    after = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    deps = next_departures(trips, sample_stop_times, platform_ids, weekday, after)
    trip_ids = {d.trip_id for d in deps}
    assert trip_ids.issubset({"WEEKDAY_TRIP_1", "WEEKDAY_TRIP_2"})

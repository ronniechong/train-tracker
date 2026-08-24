from datetime import date, datetime, timezone

from traintracker.gtfs.gtfstime import gtfs_time_to_utc
from traintracker.gtfs.schedule import (
    added_departures,
    next_departures,
    next_service_same_line,
    platforms_for_station,
)
from traintracker.gtfs.stops import Stop
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot


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


def _added_snapshot(
    trip_id="EXTRA1", route_id="R1", stop_time_updates=(), schedule_relationship="ADDED"
) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id=trip_id,
        route_id=route_id,
        start_time="08:00:00",
        start_date="20260720",
        schedule_relationship=schedule_relationship,
        stop_time_updates=stop_time_updates,
        schedule_updated_at=datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc),
        latitude=None,
        longitude=None,
        bearing=None,
        position_updated_at=None,
    )


def _stu(stop_id, departure_time=None, arrival_time=None) -> StopTimeUpdate:
    return StopTimeUpdate(
        stop_sequence=1,
        stop_id=stop_id,
        arrival_delay=None,
        arrival_time=arrival_time,
        departure_delay=None,
        departure_time=departure_time,
        schedule_relationship="SCHEDULED",
    )


def test_added_departures_includes_added_trip_calling_at_the_platform(sample_stops):
    after = datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)
    snapshot = _added_snapshot(
        stop_time_updates=(
            _stu("PLAT_A1", departure_time="1784700000"),
            _stu("PLAT_B1", arrival_time="1784700300"),
        )
    )

    deps = added_departures({"EXTRA1": snapshot}, sample_stops, frozenset({"PLAT_A1"}), after)

    assert len(deps) == 1
    dep = deps[0]
    assert dep.trip_id == "EXTRA1"
    assert dep.route_id == "R1"
    assert dep.direction_id is None
    assert dep.stop_id == "PLAT_A1"
    # Headsign is derived from the trip's FINAL stop -- PLAT_B1, not the
    # matched platform itself.
    assert dep.headsign == "B Station Platform 1"


def test_added_departures_ignores_non_added_trips(sample_stops):
    after = datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)
    scheduled = _added_snapshot(
        stop_time_updates=(_stu("PLAT_A1", departure_time="1784700000"),),
        schedule_relationship="SCHEDULED",
    )

    deps = added_departures({"T1": scheduled}, sample_stops, frozenset({"PLAT_A1"}), after)

    assert deps == []


def test_added_departures_ignores_trips_not_calling_at_this_platform(sample_stops):
    after = datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)
    snapshot = _added_snapshot(stop_time_updates=(_stu("PLAT_B1", departure_time="1784700000"),))

    deps = added_departures({"EXTRA1": snapshot}, sample_stops, frozenset({"PLAT_A1"}), after)

    assert deps == []


def test_added_departures_filters_past_departures(sample_stops):
    after = datetime(2026, 7, 20, 23, 0, tzinfo=timezone.utc)
    # 1784584800 == 2026-07-20T22:00:00Z, before `after`.
    snapshot = _added_snapshot(stop_time_updates=(_stu("PLAT_A1", departure_time="1784584800"),))

    deps = added_departures({"EXTRA1": snapshot}, sample_stops, frozenset({"PLAT_A1"}), after)

    assert deps == []


def test_added_departures_falls_back_to_stop_id_when_final_stop_unknown(sample_stops):
    after = datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)
    snapshot = _added_snapshot(
        stop_time_updates=(
            _stu("PLAT_A1", departure_time="1784700000"),
            _stu("NOT_A_KNOWN_STOP", arrival_time="1784700300"),
        )
    )

    deps = added_departures({"EXTRA1": snapshot}, sample_stops, frozenset({"PLAT_A1"}), after)

    assert deps[0].headsign == "NOT_A_KNOWN_STOP"


def test_added_departures_respects_limit_per_direction(sample_stops):
    after = datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)
    snapshots = {
        f"EXTRA{i}": _added_snapshot(
            trip_id=f"EXTRA{i}",
            stop_time_updates=(_stu("PLAT_A1", departure_time=str(1784700000 + i * 60)),),
        )
        for i in range(5)
    }

    deps = added_departures(snapshots, sample_stops, frozenset({"PLAT_A1"}), after, limit_per_direction=2)

    assert len(deps) == 2


def test_next_service_same_line_finds_soonest_connecting_trip(sample_snapshot, sample_stop_times):
    weekday = date(2026, 7, 20)  # Monday
    trips = _active_trips(sample_snapshot, weekday)
    after = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    leg = next_service_same_line(
        trips, sample_stop_times, frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), weekday, after
    )

    assert leg is not None
    assert leg.trip_id == "WEEKDAY_TRIP_1"
    assert leg.from_stop_id == "PLAT_A1"
    assert leg.to_stop_id == "PLAT_B1"
    assert leg.departure_time == gtfs_time_to_utc(weekday, "08:00:00")
    assert leg.arrival_time == gtfs_time_to_utc(weekday, "08:10:00")


def test_next_service_same_line_respects_after(sample_snapshot, sample_stop_times):
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    after = gtfs_time_to_utc(weekday, "08:05:00")

    leg = next_service_same_line(
        trips, sample_stop_times, frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), weekday, after
    )

    assert leg is None


def test_next_service_same_line_ignores_wrong_direction(sample_snapshot, sample_stop_times):
    # WEEKDAY_TRIP_2 stops at PLAT_B1 then PLAT_A1 -- the reverse order --
    # so a B->A style trip must never satisfy an A->B query even though
    # both platforms appear somewhere in its stop sequence.
    weekday = date(2026, 7, 20)
    trips = _active_trips(sample_snapshot, weekday)
    after = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    leg = next_service_same_line(
        trips, sample_stop_times, frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), weekday, after
    )

    assert leg.trip_id != "WEEKDAY_TRIP_2"


def test_next_service_same_line_no_connecting_trip_returns_none():
    leg = next_service_same_line(
        [], [], frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), date(2026, 7, 20),
        datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert leg is None

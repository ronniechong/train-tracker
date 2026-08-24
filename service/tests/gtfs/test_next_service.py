from datetime import date, datetime, timezone

from traintracker.gtfs.next_service import find_next_service_single_transfer
from traintracker.gtfs.snapshot import TripRecord
from traintracker.gtfs.stop_times import StopTimeRecord
from traintracker.gtfs.stops import Stop

SERVICE_DATE = date(2026, 7, 20)
AFTER = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)


def _stops() -> dict[str, Stop]:
    return {
        "ST_A": Stop(stop_id="ST_A", name="A Station", latitude=0.0, longitude=0.0),
        "PLAT_A1": Stop(
            stop_id="PLAT_A1", name="A Station Platform 1", latitude=0.0, longitude=0.0,
            parent_station="ST_A",
        ),
        "ST_B": Stop(stop_id="ST_B", name="B Station", latitude=0.0, longitude=0.0),
        "PLAT_B1": Stop(
            stop_id="PLAT_B1", name="B Station Platform 1", latitude=0.0, longitude=0.0,
            parent_station="ST_B",
        ),
        "ST_SX": Stop(
            stop_id="ST_SX", name="Southern Cross Railway Station", latitude=0.0, longitude=0.0
        ),
        "PLAT_SX1": Stop(
            stop_id="PLAT_SX1", name="Southern Cross Platform 1", latitude=0.0, longitude=0.0,
            parent_station="ST_SX",
        ),
    }


def _trips() -> list[TripRecord]:
    return [
        TripRecord(trip_id="LEG1", service_id="WEEKDAY", route_id="R1", trip_headsign="Southern Cross"),
        TripRecord(trip_id="LEG2", service_id="WEEKDAY", route_id="R2", trip_headsign="B Station"),
    ]


def _stop_times() -> list[StopTimeRecord]:
    return [
        StopTimeRecord("LEG1", "PLAT_A1", 1, "08:00:00", "08:00:00"),
        StopTimeRecord("LEG1", "PLAT_SX1", 2, "08:10:00", None),
        StopTimeRecord("LEG2", "PLAT_SX1", 1, "08:20:00", "08:20:00"),
        StopTimeRecord("LEG2", "PLAT_B1", 2, "08:30:00", None),
    ]


def test_single_transfer_combines_two_legs_via_curated_interchange():
    result = find_next_service_single_transfer(
        _trips(), _stop_times(), _stops(), frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), SERVICE_DATE, AFTER
    )

    assert result is not None
    assert result.first_leg.trip_id == "LEG1"
    assert result.second_leg.trip_id == "LEG2"
    assert result.interchange_station_id == "ST_SX"


def test_single_transfer_returns_none_when_no_connecting_second_leg():
    stop_times = [
        StopTimeRecord("LEG1", "PLAT_A1", 1, "08:00:00", "08:00:00"),
        StopTimeRecord("LEG1", "PLAT_SX1", 2, "08:10:00", None),
        # No LEG2 at all -- nothing continues from the interchange to B.
    ]

    result = find_next_service_single_transfer(
        _trips()[:1], stop_times, _stops(), frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), SERVICE_DATE, AFTER
    )

    assert result is None


def test_single_transfer_second_leg_must_depart_after_first_leg_arrives():
    # LEG2 departs Southern Cross at 08:05, before LEG1 even arrives
    # there (08:10) -- must not be treated as a valid connection.
    stop_times = [
        StopTimeRecord("LEG1", "PLAT_A1", 1, "08:00:00", "08:00:00"),
        StopTimeRecord("LEG1", "PLAT_SX1", 2, "08:10:00", None),
        StopTimeRecord("LEG2", "PLAT_SX1", 1, "08:05:00", "08:05:00"),
        StopTimeRecord("LEG2", "PLAT_B1", 2, "08:15:00", None),
    ]

    result = find_next_service_single_transfer(
        _trips(), stop_times, _stops(), frozenset({"PLAT_A1"}), frozenset({"PLAT_B1"}), SERVICE_DATE, AFTER
    )

    assert result is None


def test_single_transfer_skips_degenerate_interchange_at_origin_or_destination():
    # If Southern Cross itself is the origin, it must never be picked as
    # its own transfer point.
    result = find_next_service_single_transfer(
        _trips(), _stop_times(), _stops(), frozenset({"PLAT_SX1"}), frozenset({"PLAT_B1"}), SERVICE_DATE, AFTER
    )

    assert result is None

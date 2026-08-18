from traintracker.gtfs.routes import Route
from traintracker.gtfs.skip_pattern import compute_skip_stop_counts
from traintracker.gtfs.snapshot import TripRecord
from traintracker.gtfs.stop_times import StopTimeRecord
from traintracker.gtfs.stops import Stop

_ROUTE = Route(route_id="R", short_name="Test", long_name="Test - City")
_REPLACEMENT_ROUTE = Route(
    route_id="R-R:", short_name="Replacement Bus", long_name="Test - City"
)


def _stop(stop_id: str, parent_station: str | None = None) -> Stop:
    return Stop(stop_id=stop_id, name=stop_id, latitude=0.0, longitude=0.0, parent_station=parent_station)


def _st(trip_id: str, stop_id: str, seq: int) -> StopTimeRecord:
    return StopTimeRecord(
        trip_id=trip_id, stop_id=stop_id, stop_sequence=seq, arrival_time=None, departure_time=None
    )


def test_all_stops_trip_gets_zero_skips_against_a_shorter_express():
    trips = [
        TripRecord(trip_id="ALL", service_id="WD", route_id="R", direction_id=0),
        TripRecord(trip_id="EXP", service_id="WD", route_id="R", direction_id=0),
    ]
    stop_times = [
        _st("ALL", "A", 1), _st("ALL", "B", 2), _st("ALL", "C", 3), _st("ALL", "D", 4),
        _st("EXP", "A", 1), _st("EXP", "C", 2), _st("EXP", "D", 3),
    ]
    stops = {sid: _stop(sid) for sid in "ABCD"}

    counts = compute_skip_stop_counts(trips, stop_times, stops, {"R": _ROUTE})

    assert counts["ALL"] == 0
    assert counts["EXP"] == 1


def test_insufficient_comparable_trips_returns_none():
    trips = [TripRecord(trip_id="ONLY", service_id="WD", route_id="R", direction_id=0)]
    stop_times = [_st("ONLY", "A", 1), _st("ONLY", "B", 2)]
    stops = {sid: _stop(sid) for sid in "AB"}

    counts = compute_skip_stop_counts(trips, stop_times, stops, {"R": _ROUTE})

    assert counts["ONLY"] is None


def test_replacement_bus_routes_are_excluded():
    trips = [
        TripRecord(trip_id="ALL", service_id="WD", route_id="R", direction_id=0),
        TripRecord(trip_id="BUS", service_id="WD", route_id="R-R:", direction_id=0),
    ]
    stop_times = [
        _st("ALL", "A", 1), _st("ALL", "B", 2),
        _st("BUS", "A", 1), _st("BUS", "B", 2),
    ]
    stops = {sid: _stop(sid) for sid in "AB"}
    routes = {"R": _ROUTE, "R-R:": _REPLACEMENT_ROUTE}

    counts = compute_skip_stop_counts(trips, stop_times, stops, routes)

    assert "BUS" not in counts
    # Excluding the bus leaves only one comparable train trip -- no group.
    assert counts["ALL"] is None


def test_city_loop_trips_are_not_compared_against_direct_city_trips():
    # A "via Loop" trip and a "direct" trip on the same route+direction have
    # structurally different stop counts by design (extra Loop stations) --
    # they must land in separate comparison groups, not get flagged as
    # skipping stops relative to each other.
    trips = [
        TripRecord(trip_id="LOOP", service_id="WD", route_id="R", direction_id=1),
        TripRecord(trip_id="DIRECT", service_id="WD", route_id="R", direction_id=1),
    ]
    stop_times = [
        _st("LOOP", "SUBURB", 1), _st("LOOP", "MCE", 2), _st("LOOP", "FSS", 3),
        _st("DIRECT", "SUBURB", 1), _st("DIRECT", "FSS", 2),
    ]
    stops = {
        "SUBURB": _stop("SUBURB"),
        "MCE": _stop("MCE", parent_station="vic:rail:MCE"),
        "FSS": _stop("FSS", parent_station="vic:rail:FSS"),
    }

    counts = compute_skip_stop_counts(trips, stop_times, stops, {"R": _ROUTE})

    assert counts["LOOP"] is None
    assert counts["DIRECT"] is None

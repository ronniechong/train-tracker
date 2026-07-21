from datetime import datetime, timezone

from traintracker.gtfs.stops import Stop
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot
from traintracker.state.station import derive_station_state


def _snapshot(stop_time_updates, latitude=None, longitude=None) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id="trip-1",
        route_id="aus:vic:vic-02-CBE:",
        start_time="19:00:00",
        start_date="20260718",
        schedule_relationship="SCHEDULED",
        stop_time_updates=tuple(stop_time_updates),
        schedule_updated_at=datetime.fromtimestamp(1000, tz=timezone.utc),
        latitude=latitude,
        longitude=longitude,
        bearing=None,
        position_updated_at=datetime.fromtimestamp(1000, tz=timezone.utc) if latitude else None,
    )


def _stu(seq, stop_id, arrival_time=None, departure_time=None):
    return StopTimeUpdate(
        stop_sequence=seq, stop_id=stop_id,
        arrival_delay=None, arrival_time=arrival_time,
        departure_delay=None, departure_time=departure_time,
        schedule_relationship="SCHEDULED",
    )


# Three-stop trip: A (origin, departs 1000), B (arrives 1100, departs 1120),
# C (terminus, arrives 1200).
THREE_STOPS = [
    _stu(1, "A", departure_time="1000"),
    _stu(2, "B", arrival_time="1100", departure_time="1120"),
    _stu(3, "C", arrival_time="1200"),
]

STOPS = {
    "A": Stop("A", "A Station", -37.800, 144.900),
    "B": Stop("B", "B Station", -37.810, 144.950),
    "C": Stop("C", "C Station", -37.820, 145.000),
}


def _at(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_before_first_departure_is_at_origin():
    state = derive_station_state(_snapshot(THREE_STOPS), STOPS, _at(950))
    assert state.status == "at"
    assert state.at_stop_id == "A"
    assert state.geofence_confirmed is None  # no position supplied


def test_between_two_stops_computes_progress_midpoint():
    # departs A at 1000, arrives B at 1100 -> now=1050 is the midpoint
    state = derive_station_state(_snapshot(THREE_STOPS), STOPS, _at(1050))
    assert state.status == "between"
    assert state.from_stop_id == "A"
    assert state.to_stop_id == "B"
    assert state.progress == 0.5


def test_between_progress_near_departure_is_near_zero():
    state = derive_station_state(_snapshot(THREE_STOPS), STOPS, _at(1001))
    assert state.status == "between"
    assert round(state.progress, 2) == 0.01


def test_dwelling_at_intermediate_stop():
    state = derive_station_state(_snapshot(THREE_STOPS), STOPS, _at(1110))
    assert state.status == "at"
    assert state.at_stop_id == "B"


def test_after_last_departure_is_at_terminus():
    state = derive_station_state(_snapshot(THREE_STOPS), STOPS, _at(1250))
    assert state.status == "at"
    assert state.at_stop_id == "C"


def test_before_first_anchor_with_arrival_present_is_between_not_at():
    # The list's first entry carries a real arrival prediction, meaning a
    # predecessor stop existed and has been trimmed off the rolling window
    # (confirmed against real captures - this is common, not an edge case).
    # Must NOT be read as "waiting at this stop before departure".
    stus = [_stu(4, "B", arrival_time="1100", departure_time="1120")]
    state = derive_station_state(_snapshot(stus), STOPS, _at(1050))
    assert state.status == "between"
    assert state.from_stop_id is None
    assert state.to_stop_id == "B"
    assert state.progress is None


def test_after_last_anchor_with_departure_present_is_between_not_at():
    # Last entry carries a real departure, meaning a successor stop exists
    # but hasn't entered the rolling window's prediction horizon yet.
    stus = [_stu(4, "B", arrival_time="1100", departure_time="1120")]
    state = derive_station_state(_snapshot(stus), STOPS, _at(1150))
    assert state.status == "between"
    assert state.from_stop_id == "B"
    assert state.to_stop_id is None
    assert state.progress is None


def test_no_stop_time_updates_is_unknown():
    state = derive_station_state(_snapshot([]), STOPS, _at(1000))
    assert state.status == "unknown"
    assert state.geofence_confirmed is None


def test_geofence_confirms_when_position_matches_expected_stop():
    # At B's real coordinates while dwelling at B.
    state = derive_station_state(
        _snapshot(THREE_STOPS, latitude=-37.810, longitude=144.950), STOPS, _at(1110)
    )
    assert state.status == "at"
    assert state.geofence_confirmed is True


def test_geofence_disagrees_but_schedule_status_still_wins():
    # Schedule says dwelling at B, but the live fix is actually at C's
    # coordinates (e.g. a stale/carried-forward position). Status stays
    # schedule-derived; the disagreement is surfaced via geofence_confirmed,
    # not silently overridden.
    state = derive_station_state(
        _snapshot(THREE_STOPS, latitude=-37.820, longitude=145.000), STOPS, _at(1110)
    )
    assert state.status == "at"
    assert state.at_stop_id == "B"
    assert state.geofence_confirmed is False


def test_between_segment_geofence_confirms_against_either_endpoint():
    state = derive_station_state(
        _snapshot(THREE_STOPS, latitude=-37.800, longitude=144.900), STOPS, _at(1010)
    )
    assert state.status == "between"
    assert state.geofence_confirmed is True


def test_zero_dwell_stop_is_detected_as_at_not_between():
    # B has an identical arrival/departure (no dwell at all). A zero-length
    # A->B or B->C segment can never actually be entered by the pairwise
    # walk (it requires cur.departure <= now < nxt.arrival, which is
    # impossible when they're equal) - so the only way `now == 1050` can
    # resolve is landing in B's own (zero-width) dwell window.
    stus = [
        _stu(1, "A", departure_time="1000"),
        _stu(2, "B", arrival_time="1050", departure_time="1050"),
        _stu(3, "C", arrival_time="1100"),
    ]
    state = derive_station_state(_snapshot(stus), STOPS, _at(1050))
    assert state.status == "at"
    assert state.at_stop_id == "B"

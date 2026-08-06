from datetime import date, datetime, timedelta, timezone

from traintracker.state.completion import (
    ON_TIME_THRESHOLD_S,
    TripCompletionTracker,
    TripTerminus,
)
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot


class _FakeEventLog:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _at(offset_s: float) -> datetime:
    return datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


SCHEDULED_ARRIVAL = _at(600)  # 08:10:00 UTC
TERMINUS = TripTerminus(stop_id="PLAT_B1", scheduled_arrival=SCHEDULED_ARRIVAL)


def _terminus_lookup(known_trip_ids=("t1",)):
    def lookup(trip_id: str, service_date: date) -> TripTerminus | None:
        return TERMINUS if trip_id in known_trip_ids else None
    return lookup


def _snapshot(
    trip_id="t1",
    route_id="2-BEG",
    start_date="20260720",
    stop_time_updates=(),
    schedule_updated_at=_at(0),
) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id=trip_id, route_id=route_id, start_time="08:00:00", start_date=start_date,
        schedule_relationship="SCHEDULED", stop_time_updates=stop_time_updates,
        schedule_updated_at=schedule_updated_at,
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )


def _en_route_stu() -> StopTimeUpdate:
    # A non-terminus stop, not yet arrived -- distinguishes "trip is
    # tracked but nowhere near its terminus yet" from "terminus reached".
    return StopTimeUpdate(
        stop_sequence=1, stop_id="PLAT_A1",
        arrival_delay=30, arrival_time=None, departure_delay=None, departure_time=None,
        schedule_relationship=None,
    )


def _terminus_arrived_stu(arrival_delay=None, arrival_time=None) -> StopTimeUpdate:
    # Genuine terminus per station.py's own rule: arrival_time present,
    # departure genuinely absent. Real GTFS-RT stops normally carry both
    # delay and time together for an already-passed stop, so this helper
    # derives a consistent arrival_time from arrival_delay when only the
    # delay is given, rather than leaving the gating field unset.
    #
    # `arrival_time` is passed as a STRING, deliberately -- protobuf's JSON
    # mapping stringifies int64 fields (this one), unlike arrival_delay
    # (int32, stays a real number). A real int here would hide the crash
    # this type mismatch caused in production.
    if arrival_time is None and arrival_delay is not None:
        arrival_time = str(int((SCHEDULED_ARRIVAL + timedelta(seconds=arrival_delay)).timestamp()))
    return StopTimeUpdate(
        stop_sequence=2, stop_id="PLAT_B1",
        arrival_delay=arrival_delay, arrival_time=arrival_time,
        departure_delay=None, departure_time=None, schedule_relationship=None,
    )


def test_on_time_completion_uses_the_raw_arrival_delay_field():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(60))
    tracker.tick(
        {"t1": _snapshot(
            stop_time_updates=(_terminus_arrived_stu(arrival_delay=120, arrival_time=str(int(_at(720).timestamp()))),),
            schedule_updated_at=_at(720),
        )},
        _at(720),
    )

    assert len(log.events) == 1
    event = log.events[0]
    assert event.status == "on_time"
    assert event.delay_seconds == 120
    assert event.trip_id == "t1"
    assert event.route_id == "2-BEG"
    assert event.scheduled_terminus_arrival == SCHEDULED_ARRIVAL


def test_late_completion_when_delay_exceeds_threshold():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(
            stop_time_updates=(_terminus_arrived_stu(arrival_delay=ON_TIME_THRESHOLD_S + 1),),
        )},
        _at(900),
    )

    assert len(log.events) == 1
    assert log.events[0].status == "late"


def test_on_time_boundary_is_inclusive():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_terminus_arrived_stu(arrival_delay=ON_TIME_THRESHOLD_S),))},
        _at(900),
    )

    assert log.events[0].status == "on_time"


def test_delay_falls_back_to_computing_from_arrival_time_when_delay_field_absent():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())
    actual_arrival = SCHEDULED_ARRIVAL + timedelta(seconds=45)

    tracker.tick(
        {"t1": _snapshot(
            stop_time_updates=(_terminus_arrived_stu(arrival_time=str(int(actual_arrival.timestamp()))),),
        )},
        _at(900),
    )

    assert log.events[0].delay_seconds == 45
    assert log.events[0].status == "on_time"


def test_trip_with_no_static_terminus_is_never_tracked():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup(known_trip_ids=()))  # nothing resolves

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(60))
    tracker.tick({"t1": _snapshot(stop_time_updates=(_terminus_arrived_stu(arrival_delay=0),))}, _at(900))

    assert log.events == []


def test_a_stop_with_arrival_and_departure_is_not_treated_as_the_terminus():
    # Rolling-window trim artifact, not a genuine terminus (station.py's
    # exact rule) -- a departure value present means there's a successor
    # stop the window just hasn't surfaced yet.
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())
    mid_journey_stu = StopTimeUpdate(
        stop_sequence=2, stop_id="PLAT_B1", arrival_delay=0, arrival_time=str(int(_at(600).timestamp())),
        departure_delay=0, departure_time=int(_at(650).timestamp()), schedule_relationship=None,
    )

    tracker.tick({"t1": _snapshot(stop_time_updates=(mid_journey_stu,))}, _at(600))

    assert log.events == []


def test_cancelled_trip_finalizes_immediately_not_as_undetermined_gap():
    # Reliability (did it run) vs punctuality (was it on time) are separate
    # metrics -- a cancellation must not wait out UNDETERMINED_TIMEOUT_S nor
    # get scored as a punctuality miss.
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())
    cancelled_snapshot = TrainSnapshot(
        trip_id="t1", route_id="2-BEG", start_time="08:00:00", start_date="20260720",
        schedule_relationship="CANCELED", stop_time_updates=(), schedule_updated_at=_at(0),
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )

    tracker.tick({"t1": cancelled_snapshot}, _at(0))

    assert len(log.events) == 1
    event = log.events[0]
    assert event.status == "cancelled"
    assert event.actual_terminus_arrival is None
    assert event.delay_seconds is None
    assert "t1" not in tracker._pending


def test_trip_cancelled_after_already_pending_finalizes_as_cancelled_not_late():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(0))
    assert log.events == []

    cancelled_snapshot = TrainSnapshot(
        trip_id="t1", route_id="2-BEG", start_time="08:00:00", start_date="20260720",
        schedule_relationship="CANCELED", stop_time_updates=(), schedule_updated_at=_at(60),
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )
    tracker.tick({"t1": cancelled_snapshot}, _at(60))

    assert len(log.events) == 1
    assert log.events[0].status == "cancelled"


def test_undetermined_gap_after_timeout_with_no_terminus_reached():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())
    timeout = timedelta(seconds=100)

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(0), undetermined_timeout=timeout)
    # trip vanishes from TU entirely (ghosted/coverage gap) -- tick with an
    # empty snapshot dict, well past the timeout.
    tracker.tick({}, _at(200), undetermined_timeout=timeout)

    assert len(log.events) == 1
    event = log.events[0]
    assert event.status == "undetermined_gap"
    assert event.actual_terminus_arrival is None
    assert event.delay_seconds is None
    assert event.scheduled_terminus_arrival == SCHEDULED_ARRIVAL


def test_finalized_trip_is_not_reregistered_within_the_retention_window():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick({"t1": _snapshot(stop_time_updates=(_terminus_arrived_stu(arrival_delay=0),))}, _at(600))
    assert len(log.events) == 1

    # Same trip_id reappears (feed glitch) -- must not double-emit.
    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(660))

    assert len(log.events) == 1


def test_flush_force_closes_pending_trips_as_undetermined_gap():
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(0))
    tracker.flush(_at(30))

    assert len(log.events) == 1
    assert log.events[0].status == "undetermined_gap"


def test_a_cycle_with_no_fresh_tu_schedule_does_not_touch_pending_state():
    # VP-only cycle (has_schedule False) -- must not be misread as "trip
    # gone", nor advance last_touched_at, nor crash on start_date being None.
    log = _FakeEventLog()
    tracker = TripCompletionTracker(log, _terminus_lookup())
    vp_only = TrainSnapshot(
        trip_id="t1", route_id=None, start_time=None, start_date=None,
        schedule_relationship=None, stop_time_updates=(), schedule_updated_at=None,
        latitude=-37.8, longitude=144.9, bearing=0.0, position_updated_at=_at(0),
    )

    tracker.tick({"t1": _snapshot(stop_time_updates=(_en_route_stu(),))}, _at(0))
    tracker.tick({"t1": vp_only}, _at(10))

    assert log.events == []

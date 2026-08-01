from datetime import date, datetime, timedelta, timezone

from traintracker.gtfs.schedule_cache import TripTerminus
from traintracker.state.alerts import Alert, InformedEntity
from traintracker.state.delay_observation import (
    OBSERVATION_INTERVAL_S,
    DelayObservationTracker,
)
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot


class _FakeEventLog:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _at(offset_s: float) -> datetime:
    return datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


TERMINUS = TripTerminus(stop_id="PLAT_C1", scheduled_arrival=_at(1200), stop_sequence=5)


def _terminus_lookup(known_trip_ids=("t1",)):
    def lookup(trip_id: str, service_date: date) -> TripTerminus | None:
        return TERMINUS if trip_id in known_trip_ids else None
    return lookup


def _stu(stop_sequence, arrival_delay=None, departure_delay=None) -> StopTimeUpdate:
    return StopTimeUpdate(
        stop_sequence=stop_sequence, stop_id=f"PLAT_{stop_sequence}",
        arrival_delay=arrival_delay, arrival_time=None,
        departure_delay=departure_delay, departure_time=None,
        schedule_relationship=None,
    )


def _snapshot(
    trip_id="t1", route_id="2-BEG", start_date="20260720",
    schedule_relationship="SCHEDULED", stop_time_updates=(),
    schedule_updated_at=_at(0),
) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id=trip_id, route_id=route_id, start_time="08:00:00", start_date=start_date,
        schedule_relationship=schedule_relationship, stop_time_updates=stop_time_updates,
        schedule_updated_at=schedule_updated_at,
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )


def test_records_an_observation_with_delay_and_stops_remaining():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(2, arrival_delay=90),))}, _at(0), {},
    )

    assert len(log.events) == 1
    event = log.events[0]
    assert event.trip_id == "t1"
    assert event.route_id == "2-BEG"
    assert event.current_delay_s == 90
    assert event.stops_remaining == 3  # terminus stop_sequence 5 - current 2
    assert event.active_alert_flag is False


def test_prefers_arrival_delay_over_departure_delay():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(2, arrival_delay=60, departure_delay=999),))},
        _at(0), {},
    )

    assert log.events[0].current_delay_s == 60


def test_falls_back_to_departure_delay_when_arrival_delay_absent():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(2, departure_delay=45),))}, _at(0), {},
    )

    assert log.events[0].current_delay_s == 45


def test_nearest_stop_is_the_lowest_stop_sequence_in_the_rolling_window():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(
            _stu(3, arrival_delay=200),
            _stu(2, arrival_delay=90),  # lowest -- this is "nearest"
            _stu(4, arrival_delay=300),
        ))},
        _at(0), {},
    )

    assert log.events[0].current_delay_s == 90
    assert log.events[0].stops_remaining == 3


def test_no_delay_signal_on_nearest_stop_skips_the_observation():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(2),))}, _at(0), {},
    )

    assert log.events == []


def test_trip_with_no_static_terminus_is_never_observed():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup(known_trip_ids=()))

    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(2, arrival_delay=90),))}, _at(0), {},
    )

    assert log.events == []


def test_cancelled_trip_is_never_observed():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick(
        {"t1": _snapshot(
            schedule_relationship="CANCELED",
            stop_time_updates=(_stu(2, arrival_delay=90),),
        )},
        _at(0), {},
    )

    assert log.events == []


def test_trip_with_no_stop_time_updates_is_never_observed():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    tracker.tick({"t1": _snapshot(stop_time_updates=())}, _at(0), {})

    assert log.events == []


def test_a_cycle_with_no_fresh_tu_schedule_is_not_observed():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())
    vp_only = TrainSnapshot(
        trip_id="t1", route_id=None, start_time=None, start_date=None,
        schedule_relationship=None, stop_time_updates=(), schedule_updated_at=None,
        latitude=-37.8, longitude=144.9, bearing=0.0, position_updated_at=_at(0),
    )

    tracker.tick({"t1": vp_only}, _at(0), {})

    assert log.events == []


def test_respects_the_observation_interval_between_repeat_observations():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())
    snapshot = _snapshot(stop_time_updates=(_stu(2, arrival_delay=90),))

    tracker.tick({"t1": snapshot}, _at(0), {})
    tracker.tick({"t1": snapshot}, _at(OBSERVATION_INTERVAL_S - 1), {})  # too soon
    tracker.tick({"t1": snapshot}, _at(OBSERVATION_INTERVAL_S + 1), {})  # due again

    assert len(log.events) == 2


def test_active_alert_flag_true_when_an_alert_matches_the_route():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())
    alert = Alert(
        id="A1", cause="OTHER_CAUSE", effect="SIGNIFICANT_DELAYS", header_text="Delays",
        description_text=None, url=None, active_periods=(),
        informed_entities=(InformedEntity(route_id="2-BEG", stop_id=None, direction_id=None),),
    )

    tracker.tick(
        {"t1": _snapshot(route_id="2-BEG", stop_time_updates=(_stu(2, arrival_delay=90),))},
        _at(0), {"A1": alert},
    )

    assert log.events[0].active_alert_flag is True


def test_active_alert_flag_false_when_no_alert_matches_the_route():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())
    alert = Alert(
        id="A1", cause="OTHER_CAUSE", effect="SIGNIFICANT_DELAYS", header_text="Delays",
        description_text=None, url=None, active_periods=(),
        informed_entities=(InformedEntity(route_id="2-CRB", stop_id=None, direction_id=None),),
    )

    tracker.tick(
        {"t1": _snapshot(route_id="2-BEG", stop_time_updates=(_stu(2, arrival_delay=90),))},
        _at(0), {"A1": alert},
    )

    assert log.events[0].active_alert_flag is False


def test_stops_remaining_never_negative_skips_the_observation():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())

    # Nearest stop's stop_sequence (9) is past the terminus's (5) -- a
    # rolling-window/terminus mismatch edge case, must not record a
    # negative feature.
    tracker.tick(
        {"t1": _snapshot(stop_time_updates=(_stu(9, arrival_delay=90),))}, _at(0), {},
    )

    assert log.events == []


def test_last_observed_state_is_evicted_after_the_retention_window():
    log = _FakeEventLog()
    tracker = DelayObservationTracker(log, _terminus_lookup())
    snapshot = _snapshot(stop_time_updates=(_stu(2, arrival_delay=90),))

    tracker.tick({"t1": snapshot}, _at(0), {})
    assert "t1" in tracker._last_observed

    # Long past LAST_OBSERVED_RETENTION_S with no further ticks for t1 at all.
    tracker.tick({}, _at(7 * 60 * 60), {})

    assert "t1" not in tracker._last_observed

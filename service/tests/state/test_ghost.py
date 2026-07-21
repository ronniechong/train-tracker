from datetime import datetime, timedelta, timezone

from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.ghost import COASTING_TIMEOUT_S, TrainLifecycleTracker
from traintracker.state.merge import TrainSnapshot


def _at(epoch_offset_s):
    return datetime.fromtimestamp(1_000_000 + epoch_offset_s, tz=timezone.utc)


def _snap(lat, lon, ts) -> TrainSnapshot:
    return TrainSnapshot(
        trip_id="trip-1", route_id=None, start_time=None, start_date=None,
        schedule_relationship=None, stop_time_updates=(), schedule_updated_at=None,
        latitude=lat, longitude=lon, bearing=None, position_updated_at=ts,
    )


def test_stays_live_while_position_keeps_arriving():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    assert tracker.status_of("trip-1") == "live"

    tracker.tick({"trip-1": _snap(-37.8, 144.91, _at(10))}, _at(10))
    assert tracker.status_of("trip-1") == "live"
    assert log.events == []


def test_becomes_coasting_then_ghost_as_time_elapses_without_position():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(30))
    assert tracker.status_of("trip-1") == "coasting"

    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"
    assert log.events == []  # no reappearance yet, nothing to log


def test_reappearance_emits_ghost_event_with_both_endpoints():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.80, 144.90, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"

    reappear_ts = _at(COASTING_TIMEOUT_S + 40)
    tracker.tick({"trip-1": _snap(-37.90, 145.00, reappear_ts)}, reappear_ts)
    assert tracker.status_of("trip-1") == "live"

    assert len(log.events) == 1
    event = log.events[0]
    assert event.trip_id == "trip-1"
    assert event.last_seen_position == (-37.80, 144.90)
    assert event.reappear_position == (-37.90, 145.00)
    assert event.reappeared_at == reappear_ts
    # ghost_started_at is stamped at the tick that first observed the
    # threshold crossed (t=100), not the true instant it crossed (t=90) -
    # an approximation of ~1 poll interval, acceptable at real cadence.
    assert event.ghost_duration_s == 30.0
    assert event.loop_contained is False
    assert event.backoff_overlapped is False


def test_loop_contained_true_when_both_endpoints_inside_bbox():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    inside = (-37.810, 144.965)  # inside CITY_LOOP_BBOX
    tracker.tick({"trip-1": _snap(*inside, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))

    reappear_ts = _at(COASTING_TIMEOUT_S + 20)
    tracker.tick({"trip-1": _snap(inside[0], inside[1], reappear_ts)}, reappear_ts)

    assert log.events[0].loop_contained is True


def test_backoff_freezes_the_coasting_clock():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    # A long stretch of backoff-skipped ticks that would, if counted,
    # easily exceed the ghost threshold - must NOT ghost the train.
    tracker.tick({}, _at(20), backoff_active=True)
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 100), backoff_active=True)
    assert tracker.status_of("trip-1") == "coasting"

    # Now backoff clears; elapsed only starts counting from here.
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 110))
    assert tracker.status_of("trip-1") == "coasting"


def test_backoff_overlapped_flag_recorded_on_the_event():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(10), backoff_active=True)
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))  # non-backoff tick pushes it into ghost
    assert tracker.status_of("trip-1") == "ghost"

    reappear_ts = _at(COASTING_TIMEOUT_S + 20)
    tracker.tick({"trip-1": _snap(-37.9, 145.0, reappear_ts)}, reappear_ts)

    assert log.events[0].backoff_overlapped is True


def test_trip_seen_only_in_tu_from_the_start_is_ghost_not_coasting():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    schedule_only = TrainSnapshot(
        trip_id="trip-2", route_id="r", start_time="19:00:00", start_date="20260718",
        schedule_relationship="SCHEDULED", stop_time_updates=(), schedule_updated_at=_at(0),
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )
    tracker.tick({"trip-2": schedule_only}, _at(0))
    # "coasting" implies a real last-known fix to keep showing, which we
    # never had - must go straight to ghost, not invent a coast phase.
    assert tracker.status_of("trip-2") == "ghost"


def test_flush_force_closes_open_ghost_episodes_with_no_reappearance():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"

    tracker.flush(at=_at(COASTING_TIMEOUT_S + 500))

    assert len(log.events) == 1
    event = log.events[0]
    assert event.reappeared_at is None
    assert event.reappear_position is None
    assert event.loop_contained is False


def test_status_of_unknown_trip_is_none():
    tracker = TrainLifecycleTracker(InMemoryEventLog())
    assert tracker.status_of("nonexistent") is None

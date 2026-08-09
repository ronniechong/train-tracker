from datetime import datetime, timedelta, timezone

from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.ghost import COASTING_TIMEOUT_S, MAX_GHOST_AGE_S, TrainLifecycleTracker
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
    # ghost_started_at is stamped at the tick that observes the threshold
    # crossed, not the true crossing instant - approximate by ~1 poll interval.
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


def test_view_of_exposes_ghost_started_at_only_while_ghosted():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    assert tracker.view_of("trip-1").ghost_started_at is None  # live

    tracker.tick({}, _at(30))
    assert tracker.view_of("trip-1").ghost_started_at is None  # coasting

    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    view = tracker.view_of("trip-1")
    assert view.status == "ghost"
    assert view.ghost_started_at == _at(COASTING_TIMEOUT_S + 10)
    assert view.last_position == (-37.8, 144.9)

    assert tracker.view_of("never-seen") is None


def test_backoff_freezes_the_coasting_clock():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    # Backoff-skipped ticks must not count toward the ghost threshold.
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
    # No real last-known fix ever existed, so this must go straight to
    # ghost rather than inventing a coast phase.
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


def test_evicts_ghost_after_max_ghost_age_since_last_touched():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"

    # last_touched_at must stay frozen at the last genuine feed mention;
    # empty-snapshot ticks must not refresh it, or eviction would never trigger.
    tracker.tick({}, _at(MAX_GHOST_AGE_S + 1))
    assert tracker.status_of("trip-1") is None
    assert tracker.all_tracked() == ()

    # Eviction of a still-ghosted trip must close its episode, same as
    # flush() does, so the event log doesn't keep a dangling one.
    assert len(log.events) == 1
    event = log.events[0]
    assert event.trip_id == "trip-1"
    assert event.reappeared_at is None
    assert event.reappear_position is None


def test_evicts_tu_only_ghost_with_no_last_seen_at():
    """A trip seen only in Trip Updates never gets a `last_seen_at` (that
    field is VP-confirmation-only), so eviction must key on `last_touched_at`
    (set from any feed) instead."""
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    schedule_only = TrainSnapshot(
        trip_id="trip-2", route_id="r", start_time="19:00:00", start_date="20260718",
        schedule_relationship="SCHEDULED", stop_time_updates=(), schedule_updated_at=_at(0),
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )
    tracker.tick({"trip-2": schedule_only}, _at(0))
    assert tracker.status_of("trip-2") == "ghost"

    tracker.tick({}, _at(MAX_GHOST_AGE_S + 1))
    assert tracker.status_of("trip-2") is None
    assert tracker.all_tracked() == ()


def test_all_tracked_excludes_evicted_trips_but_keeps_recent_ones():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"old": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("old") == "ghost"

    # "new" appears in the same tick that pushes "old" past MAX_GHOST_AGE_S;
    # only "old" (untouched since it was last seen) should be evicted.
    later = _at(MAX_GHOST_AGE_S + 1)
    tracker.tick({"new": _snap(-37.9, 145.0, later)}, later)

    trip_ids = {t.trip_id for t in tracker.all_tracked()}
    assert trip_ids == {"new"}


def test_reappearance_reason_is_reappeared():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    reappear_ts = _at(COASTING_TIMEOUT_S + 20)
    tracker.tick({"trip-1": _snap(-37.9, 145.0, reappear_ts)}, reappear_ts)

    assert log.events[0].reason == "reappeared"


def test_timed_out_eviction_reason_is_timed_out():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    tracker.tick({}, _at(MAX_GHOST_AGE_S + 1))

    assert log.events[0].reason == "timed_out"


def test_flush_reason_is_flushed():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    tracker.flush(at=_at(COASTING_TIMEOUT_S + 500))

    assert log.events[0].reason == "flushed"


def test_mark_resolved_on_already_ghosted_trip_evicts_immediately():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"

    # Confirmed completed well before MAX_GHOST_AGE_S would otherwise fire.
    resolved_at = _at(COASTING_TIMEOUT_S + 20)
    tracker.mark_resolved("trip-1", "completed", resolved_at)

    assert tracker.status_of("trip-1") is None
    assert len(log.events) == 1
    event = log.events[0]
    assert event.reason == "completed"
    assert event.reappeared_at is None
    assert event.reappear_position is None
    assert event.ghost_duration_s == 10.0


def test_mark_resolved_cancelled_on_already_ghosted_trip():
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))

    tracker.mark_resolved("trip-1", "cancelled", _at(COASTING_TIMEOUT_S + 15))

    assert tracker.status_of("trip-1") is None
    assert log.events[0].reason == "cancelled"


def test_mark_resolved_while_still_coasting_applies_at_ghost_transition():
    """A trip confirmed completed/cancelled while it's still coasting (not
    yet ghosted) must not be touched immediately -- it's still visibly
    live/coasting on the map. The resolution is remembered and applied the
    moment it would otherwise transition into ghost, well before
    MAX_GHOST_AGE_S, instead of showing as an unexplained ghost first."""
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.tick({}, _at(30))
    assert tracker.status_of("trip-1") == "coasting"

    tracker.mark_resolved("trip-1", "completed", _at(35))
    # Still coasting immediately after -- not evicted out from under a
    # currently-visible train.
    assert tracker.status_of("trip-1") == "coasting"
    assert log.events == []

    # Crossing what would have been the ghost threshold now evicts with the
    # remembered reason instead of ever showing "ghost".
    tracker.tick({}, _at(COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") is None
    assert len(log.events) == 1
    assert log.events[0].reason == "completed"


def test_mark_resolved_while_never_seen_live_applies_immediately_to_ghost():
    """A TU-only trip goes straight to ghost with no coasting phase (see
    test_trip_seen_only_in_tu_from_the_start_is_ghost_not_coasting) -- a
    resolution recorded before its first tick must apply there too."""
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.mark_resolved("trip-2", "cancelled", _at(0))
    assert tracker.status_of("trip-2") is None  # not tracked yet, nothing to do
    assert log.events == []

    schedule_only = TrainSnapshot(
        trip_id="trip-2", route_id="r", start_time="19:00:00", start_date="20260718",
        schedule_relationship="SCHEDULED", stop_time_updates=(), schedule_updated_at=_at(0),
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )
    tracker.tick({"trip-2": schedule_only}, _at(1))

    assert tracker.status_of("trip-2") is None
    assert len(log.events) == 1
    assert log.events[0].reason == "cancelled"


def test_mark_resolved_does_not_affect_trip_that_stays_live():
    """A resolution recorded while a trip is live/coasting must not fire at
    all if the trip keeps reporting a live position -- it should never
    ghost, so the remembered resolution should never be consulted."""
    log = InMemoryEventLog()
    tracker = TrainLifecycleTracker(log)

    tracker.tick({"trip-1": _snap(-37.8, 144.9, _at(0))}, _at(0))
    tracker.mark_resolved("trip-1", "completed", _at(5))

    tracker.tick({"trip-1": _snap(-37.8, 144.91, _at(10))}, _at(10))
    assert tracker.status_of("trip-1") == "live"
    assert log.events == []

    # And a later, unrelated ghost episode for the same trip_id must not be
    # misclassified by the stale resolution -- reappearing live clears it.
    tracker.tick({}, _at(10 + COASTING_TIMEOUT_S + 10))
    assert tracker.status_of("trip-1") == "ghost"
    assert log.events == []

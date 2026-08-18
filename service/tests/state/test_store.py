from datetime import date, datetime, timedelta, timezone

from traintracker.state.completion import TripCompletionTracker, TripTerminus
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.ghost import MAX_GHOST_AGE_S
from traintracker.state.headway import HeadwayInfo
from traintracker.state.store import StateStore


def _tu_feed(header_ts, trip_id, schedule_relationship="SCHEDULED"):
    return {
        "header": {"timestamp": header_ts},
        "entity": [{
            "id": trip_id,
            "trip_update": {
                "trip": {"trip_id": trip_id, "start_time": "19:00:00",
                         "start_date": "20260718", "schedule_relationship": schedule_relationship,
                         "route_id": "r"},
                "stop_time_update": [],
            },
        }],
    }


def _vp_feed(header_ts, trip_id, timestamp):
    return {
        "header": {"timestamp": header_ts},
        "entity": [{
            "id": trip_id,
            "vehicle": {
                "trip": {"trip_id": trip_id, "route_id": "r"},
                "position": {"latitude": -37.8, "longitude": 144.9, "bearing": 0.0},
                "timestamp": timestamp,
                "vehicle": {"id": "v"},
            },
        }],
    }


def _at(offset_s):
    return datetime.fromtimestamp(1_000_000 + offset_s, tz=timezone.utc)


def test_persistent_discrepancy_is_logged_once_not_once_per_tick():
    discrepancy_log = InMemoryEventLog()
    store = StateStore(discrepancy_log, InMemoryEventLog())

    empty_tu = {"header": {"timestamp": "1000000"}, "entity": []}
    vp = _vp_feed("1000000", "trip-1", "1000000")

    for i in range(5):
        store.ingest(empty_tu, vp, _at(i * 10))

    matching = [e for e in discrepancy_log.events if e.trip_id == "trip-1"]
    assert len(matching) == 1
    assert matching[0].discrepancy_type == "vp_without_tu"


def test_discrepancy_resolving_then_recurring_is_logged_as_a_new_episode():
    discrepancy_log = InMemoryEventLog()
    store = StateStore(discrepancy_log, InMemoryEventLog())

    empty_tu = {"header": {"timestamp": "1000000"}, "entity": []}
    vp = _vp_feed("1000000", "trip-1", "1000000")
    tu_with_match = _tu_feed("1000010", "trip-1")

    store.ingest(empty_tu, vp, _at(0))  # mismatch starts
    store.ingest(tu_with_match, vp, _at(10))  # resolves (now present in both)
    store.ingest(empty_tu, vp, _at(20))  # mismatch recurs

    matching = [e for e in discrepancy_log.events if e.trip_id == "trip-1"]
    assert len(matching) == 2


def test_ingest_also_drives_the_lifecycle_tracker():
    store = StateStore(InMemoryEventLog(), InMemoryEventLog())
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))
    assert store.status_of("trip-1") == "live"


def test_on_tick_hook_fires_with_the_fresh_all_tracked_result():
    calls = []
    store = StateStore(InMemoryEventLog(), InMemoryEventLog(), on_tick=calls.append)
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))

    assert len(calls) == 1
    assert [t.trip_id for t in calls[0]] == ["trip-1"]
    assert calls[0][0].status == "live"


def test_on_tick_hook_is_optional():
    # Default None -- must not raise for the common case of no observer.
    store = StateStore(InMemoryEventLog(), InMemoryEventLog())
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))  # no exception


def _terminus_lookup(trip_id: str, service_date: date) -> TripTerminus | None:
    return TripTerminus(stop_id="TERM", scheduled_arrival=_at(600))


def test_cancelled_trip_fades_immediately_instead_of_waiting_max_ghost_age():
    """M11: TripCompletionTracker's independent CANCELED detection must
    reach the ghost tracker the same tick, fading a never-seen-live (TU-only,
    straight-to-ghost) trip well before MAX_GHOST_AGE_S -- not leaving it as
    an unexplained ghost for up to 2 hours."""
    ghost_log = InMemoryEventLog()
    completion_tracker = TripCompletionTracker(InMemoryEventLog(), _terminus_lookup)
    store = StateStore(InMemoryEventLog(), ghost_log, completion_tracker=completion_tracker)

    tu_cancelled = _tu_feed("1000000", "trip-1", schedule_relationship="CANCELED")
    empty_vp = {"header": {"timestamp": "1000000"}, "entity": []}

    store.ingest(tu_cancelled, empty_vp, _at(0))

    # Never seen a live position -- straight to ghost -- then immediately
    # resolved+evicted in the very same tick, not left ghosted.
    assert store.status_of("trip-1") is None
    assert len(ghost_log.events) == 1
    event = ghost_log.events[0]
    assert event.reason == "cancelled"
    # A trip never seen with a live position never gets a `ghost_started_at`
    # stamp (see ghost.py's "never seen live" branch) -- pre-existing
    # invariant, unaffected by this milestone; duration is honestly unknown.
    assert event.ghost_duration_s is None

    # Confirm this really is far short of the 2-hour timeout that would
    # otherwise apply -- the whole point of this milestone.
    assert 0.0 < MAX_GHOST_AGE_S


class _RecordingCompletionTracker:
    def __init__(self):
        self.tick_calls = []
        self.flush_calls = []

    def tick(self, snapshots, cycle_time):
        self.tick_calls.append((snapshots, cycle_time))
        return []

    def flush(self, at):
        self.flush_calls.append(at)


def test_completion_tracker_is_optional():
    # Default None -- must not raise, same convention as on_tick.
    store = StateStore(InMemoryEventLog(), InMemoryEventLog())
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))  # no exception
    store.flush(_at(10))  # no exception


def test_completion_tracker_ticks_on_ingest_and_flushes_on_flush():
    tracker = _RecordingCompletionTracker()
    store = StateStore(InMemoryEventLog(), InMemoryEventLog(), completion_tracker=tracker)
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))
    store.flush(_at(10))

    assert len(tracker.tick_calls) == 1
    assert "trip-1" in tracker.tick_calls[0][0]
    assert tracker.tick_calls[0][1] == _at(0)
    assert tracker.flush_calls == [_at(10)]


class _RecordingDelayObservationTracker:
    def __init__(self):
        self.tick_calls = []

    def tick(self, snapshots, cycle_time, latest_alerts):
        self.tick_calls.append((snapshots, cycle_time, latest_alerts))


def test_delay_observation_tracker_is_optional():
    # Default None -- must not raise, same convention as completion_tracker.
    store = StateStore(InMemoryEventLog(), InMemoryEventLog())
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))  # no exception


def test_delay_observation_tracker_ticks_on_ingest_with_fresh_alerts():
    tracker = _RecordingDelayObservationTracker()
    store = StateStore(InMemoryEventLog(), InMemoryEventLog(), delay_observation_tracker=tracker)
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")
    sa = {"header": {"timestamp": "1000000"}, "entity": []}

    store.ingest(tu, vp, _at(0), sa_feed=sa)

    assert len(tracker.tick_calls) == 1
    assert "trip-1" in tracker.tick_calls[0][0]
    assert tracker.tick_calls[0][1] == _at(0)
    # The SAME dict this ingest() call just set from the SA feed, not a
    # stale/empty one -- the whole reason this tracker is ticked from
    # inside ingest() rather than by a separate caller.
    assert tracker.tick_calls[0][2] is store.latest_alerts


class _RecordingHeadwayTracker:
    def __init__(self):
        self.tick_calls = []

    def tick(self, snapshots, cycle_time):
        self.tick_calls.append((snapshots, cycle_time))

    def headway_for(self, stop_id, route_id, direction_id, now):
        return HeadwayInfo(
            average_headway_seconds=480.0, sample_size=4,
            seconds_since_last_arrival=60, gap_detected=False,
        )


def test_headway_tracker_is_optional():
    # Default None -- must not raise, same convention as the other trackers.
    store = StateStore(InMemoryEventLog(), InMemoryEventLog())
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))  # no exception
    assert store.headway_for("PLAT_A", "r", 0, _at(0)) is None


def test_headway_tracker_ticks_on_ingest_and_is_queryable():
    tracker = _RecordingHeadwayTracker()
    store = StateStore(InMemoryEventLog(), InMemoryEventLog(), headway_tracker=tracker)
    tu = _tu_feed("1000000", "trip-1")
    vp = _vp_feed("1000000", "trip-1", "1000000")

    store.ingest(tu, vp, _at(0))

    assert len(tracker.tick_calls) == 1
    assert "trip-1" in tracker.tick_calls[0][0]
    assert tracker.tick_calls[0][1] == _at(0)

    info = store.headway_for("PLAT_A", "r", 0, _at(0))
    assert info.sample_size == 4

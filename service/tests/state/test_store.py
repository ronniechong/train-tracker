from datetime import datetime, timedelta, timezone

from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore


def _tu_feed(header_ts, trip_id):
    return {
        "header": {"timestamp": header_ts},
        "entity": [{
            "id": trip_id,
            "trip_update": {
                "trip": {"trip_id": trip_id, "start_time": "19:00:00",
                         "start_date": "20260718", "schedule_relationship": "SCHEDULED",
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


class _RecordingCompletionTracker:
    def __init__(self):
        self.tick_calls = []
        self.flush_calls = []

    def tick(self, snapshots, cycle_time):
        self.tick_calls.append((snapshots, cycle_time))

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

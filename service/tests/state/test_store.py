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

import sqlite3
from datetime import date, datetime, timezone

import pytest

from traintracker.gtfs.pinning import PinManifest
from traintracker.history.store import HistoryStore
from traintracker.poller.breaker import PollGapEvent
from traintracker.state.completion import TripCompletionEvent
from traintracker.state.delay_observation import DelayObservationEvent
from traintracker.state.ghost import GhostEvent
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10, mm=0):
    # 10am UTC is safely after the 3am local boundary year-round in Melbourne.
    return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)


def test_rotate_creates_a_file_named_for_the_service_date(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    assert store.service_date == date(2026, 7, 20)
    assert store.partition_path(date(2026, 7, 20)).exists()


def test_rotate_is_a_noop_within_the_same_service_date(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20, 10, 0))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="route_id_mismatch",
            tu_value="2", vp_value="3",
        )
    )
    store.rotate(_at(2026, 7, 20, 11, 0))  # still the same service_date
    assert store.counts()["discrepancy_events"] == 1


def test_recording_before_rotate_raises(tmp_path):
    store = HistoryStore(tmp_path)
    event = DiscrepancyEvent(
        trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="vp_without_tu",
        tu_value=None, vp_value="2",
    )
    with pytest.raises(RuntimeError):
        store.discrepancy_log.record(event)


def test_rotating_to_a_new_service_date_opens_a_second_file(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.rotate(_at(2026, 7, 21))
    assert store.partition_path(date(2026, 7, 20)).exists()
    assert store.partition_path(date(2026, 7, 21)).exists()
    assert store.service_date == date(2026, 7, 21)


def test_reopening_an_existing_partition_does_not_duplicate_the_meta_row(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.rotate(_at(2026, 7, 21))
    store.rotate(_at(2026, 7, 20))  # back to a day already on disk (e.g. after a restart)

    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    rows = conn.execute("SELECT service_date FROM meta").fetchall()
    conn.close()
    assert rows == [("2026-07-20",)]


def test_partition_is_paired_with_the_pinned_static_snapshot_digest(tmp_path):
    manifest = PinManifest(tmp_path / "pins.json")
    manifest.pin_digest(date(2026, 7, 20), "abc123")
    store = HistoryStore(tmp_path / "history", pin_manifest=manifest)

    store.rotate(_at(2026, 7, 20))

    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    digest = conn.execute("SELECT static_snapshot_digest FROM meta").fetchone()[0]
    conn.close()
    assert digest == "abc123"


def test_partition_has_no_digest_when_nothing_is_pinned_yet(tmp_path):
    manifest = PinManifest(tmp_path / "pins.json")
    store = HistoryStore(tmp_path / "history", pin_manifest=manifest)

    store.rotate(_at(2026, 7, 20))

    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    digest = conn.execute("SELECT static_snapshot_digest FROM meta").fetchone()[0]
    conn.close()
    assert digest is None


def test_reopening_a_partition_predating_the_reason_column_backfills_it(tmp_path):
    # Simulates a partition file written by an older build, before
    # `ghost_events.reason` existed -- CREATE TABLE IF NOT EXISTS alone
    # would leave it stuck on the old schema forever.
    history_dir = tmp_path
    history_dir.mkdir(exist_ok=True)
    path = history_dir / "2026-07-20.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE ghost_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            trip_id TEXT NOT NULL,
            last_seen_at TEXT,
            last_seen_lat REAL,
            last_seen_lon REAL,
            reappeared_at TEXT,
            reappear_lat REAL,
            reappear_lon REAL,
            loop_contained INTEGER NOT NULL,
            ghost_duration_s REAL,
            backoff_overlapped INTEGER NOT NULL
        )
        """
    )
    conn.close()

    store = HistoryStore(history_dir)
    store.rotate(_at(2026, 7, 20))
    store.ghost_log.record(
        GhostEvent(
            trip_id="t1", last_seen_at=None, last_seen_position=None,
            reappeared_at=None, reappear_position=None, loop_contained=False,
            ghost_duration_s=None, backoff_overlapped=False, reason="reappeared",
        )
    )

    conn = sqlite3.connect(path)
    row = conn.execute("SELECT trip_id, reason FROM ghost_events").fetchone()
    conn.close()
    assert row == ("t1", "reappeared")


def test_discrepancy_event_round_trips(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="route_id_mismatch",
            tu_value="2", vp_value="3",
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT trip_id, discrepancy_type, tu_value, vp_value FROM discrepancy_events"
    ).fetchone()
    conn.close()
    assert row == ("t1", "route_id_mismatch", "2", "3")


def test_ghost_event_round_trips_including_none_positions(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.ghost_log.record(
        GhostEvent(
            trip_id="t1", last_seen_at=None, last_seen_position=None,
            reappeared_at=None, reappear_position=None, loop_contained=False,
            ghost_duration_s=None, backoff_overlapped=False, reason="timed_out",
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT trip_id, last_seen_lat, reappear_lon, loop_contained, backoff_overlapped "
        "FROM ghost_events"
    ).fetchone()
    conn.close()
    assert row == ("t1", None, None, 0, 0)


def test_poll_gap_event_round_trips(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.gap_log.record(
        PollGapEvent(
            started_at=_at(2026, 7, 20, 10, 0), ended_at=_at(2026, 7, 20, 10, 5),
            reason="circuit_breaker", consecutive_failures=3, max_level_reached_s=60.0,
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT reason, consecutive_failures, max_level_reached_s FROM poll_gap_events"
    ).fetchone()
    conn.close()
    assert row == ("circuit_breaker", 3, 60.0)


def test_counts_reflects_currently_open_partition_only(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="2",
        )
    )
    store.rotate(_at(2026, 7, 21))  # rotate away -- new day starts empty
    assert store.counts() == {
        "discrepancy_events": 0, "ghost_events": 0, "poll_gap_events": 0,
        "trip_completion_events": 0, "delay_observation_events": 0,
    }


def test_trip_completion_event_round_trips(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t1", route_id="2-BEG", service_date="2026-07-20",
            scheduled_terminus_arrival=_at(2026, 7, 20, 10, 0),
            actual_terminus_arrival=_at(2026, 7, 20, 10, 3),
            delay_seconds=180, status="on_time",
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT trip_id, route_id, service_date, delay_seconds, status FROM trip_completion_events"
    ).fetchone()
    conn.close()
    assert row == ("t1", "2-BEG", "2026-07-20", 180, "on_time")


def test_trip_completion_event_round_trips_undetermined_gap(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t2", route_id=None, service_date="2026-07-20",
            scheduled_terminus_arrival=_at(2026, 7, 20, 10, 0),
            actual_terminus_arrival=None, delay_seconds=None, status="undetermined_gap",
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT route_id, actual_terminus_arrival, delay_seconds, status FROM trip_completion_events"
    ).fetchone()
    conn.close()
    assert row == (None, None, None, "undetermined_gap")


def test_close_allows_a_fresh_rotate_afterward(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.close()
    assert store.service_date is None
    store.rotate(_at(2026, 7, 20))
    assert store.counts()["discrepancy_events"] == 0


def test_read_completion_events_spans_multiple_partitions(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t1", route_id="2-BEG", service_date="2026-07-20",
            scheduled_terminus_arrival=_at(2026, 7, 20, 10, 0),
            actual_terminus_arrival=_at(2026, 7, 20, 10, 3),
            delay_seconds=180, status="on_time",
        )
    )
    store.rotate(_at(2026, 7, 21))
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t2", route_id="2-CRB", service_date="2026-07-21",
            scheduled_terminus_arrival=_at(2026, 7, 21, 9, 0),
            actual_terminus_arrival=None, delay_seconds=None, status="cancelled",
        )
    )

    window = store.read_completion_events([date(2026, 7, 20), date(2026, 7, 21)])

    assert {e.trip_id for e in window.events} == {"t1", "t2"}
    assert window.days_covered == (date(2026, 7, 20), date(2026, 7, 21))
    assert window.days_missing == ()
    # Full round trip, not just presence -- datetimes and None fields intact.
    t1 = next(e for e in window.events if e.trip_id == "t1")
    assert t1.scheduled_terminus_arrival == _at(2026, 7, 20, 10, 0)
    assert t1.actual_terminus_arrival == _at(2026, 7, 20, 10, 3)
    assert t1.status == "on_time"
    t2 = next(e for e in window.events if e.trip_id == "t2")
    assert t2.actual_terminus_arrival is None
    assert t2.status == "cancelled"


def test_read_completion_events_reports_a_missing_partition_honestly(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t1", route_id="2-BEG", service_date="2026-07-20",
            scheduled_terminus_arrival=_at(2026, 7, 20, 10, 0),
            actual_terminus_arrival=_at(2026, 7, 20, 10, 3),
            delay_seconds=180, status="on_time",
        )
    )
    # 2026-07-21's partition was never opened at all -- e.g. the poller was
    # down that whole service_date. A real gap, distinct from "opened but
    # zero events."

    window = store.read_completion_events([date(2026, 7, 20), date(2026, 7, 21)])

    assert window.days_covered == (date(2026, 7, 20),)
    assert window.days_missing == (date(2026, 7, 21),)
    assert [e.trip_id for e in window.events] == ["t1"]


def test_read_completion_events_counts_an_opened_but_empty_partition_as_covered(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))  # opened, but nothing ever recorded that day

    window = store.read_completion_events([date(2026, 7, 20)])

    assert window.days_covered == (date(2026, 7, 20),)
    assert window.days_missing == ()
    assert window.events == ()


def test_read_completion_events_does_not_touch_the_live_writer_connection(tmp_path):
    # The currently-open (today's) partition must remain fully writable
    # after a cross-partition read touches an EARLIER, already-closed
    # partition -- the read-only connections must not interfere with it.
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.rotate(_at(2026, 7, 21))

    store.read_completion_events([date(2026, 7, 20)])

    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 21), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="2",
        )
    )
    assert store.counts()["discrepancy_events"] == 1


def test_delay_observation_event_round_trips(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.delay_observation_log.record(
        DelayObservationEvent(
            trip_id="t1", route_id="2-BEG", service_date="2026-07-20",
            observed_at=_at(2026, 7, 20, 10, 2),
            current_delay_s=90, stops_remaining=3, active_alert_flag=True,
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT trip_id, route_id, service_date, current_delay_s, stops_remaining, "
        "active_alert_flag FROM delay_observation_events"
    ).fetchone()
    conn.close()
    assert row == ("t1", "2-BEG", "2026-07-20", 90, 3, 1)


def test_delay_observation_event_round_trips_no_route_id_no_alert(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.delay_observation_log.record(
        DelayObservationEvent(
            trip_id="t2", route_id=None, service_date="2026-07-20",
            observed_at=_at(2026, 7, 20, 10, 4),
            current_delay_s=0, stops_remaining=1, active_alert_flag=False,
        )
    )
    conn = sqlite3.connect(store.partition_path(date(2026, 7, 20)))
    row = conn.execute(
        "SELECT route_id, active_alert_flag FROM delay_observation_events"
    ).fetchone()
    conn.close()
    assert row == (None, 0)


def test_counts_includes_delay_observation_events(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.delay_observation_log.record(
        DelayObservationEvent(
            trip_id="t1", route_id="2-BEG", service_date="2026-07-20",
            observed_at=_at(2026, 7, 20, 10, 2),
            current_delay_s=90, stops_remaining=3, active_alert_flag=False,
        )
    )
    assert store.counts()["delay_observation_events"] == 1


def test_read_completion_events_treats_a_partition_predating_the_table_as_missing(tmp_path):
    # A partition file predating trip_completion_events, never reopened by
    # rotate() since, never gets the table created in it. Must be treated
    # the same as a missing file -- an honest gap, not a crash.
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    old_partition = history_dir / "2026-07-20.db"
    conn = sqlite3.connect(old_partition)
    conn.execute("CREATE TABLE discrepancy_events (id INTEGER PRIMARY KEY)")
    conn.close()

    store = HistoryStore(history_dir)
    window = store.read_completion_events([date(2026, 7, 20)])

    assert window.days_covered == ()
    assert window.days_missing == (date(2026, 7, 20),)
    assert window.events == ()

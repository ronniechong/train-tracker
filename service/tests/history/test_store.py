import sqlite3
from datetime import date, datetime, timezone

import pytest

from traintracker.gtfs.pinning import PinManifest
from traintracker.history.store import HistoryStore
from traintracker.poller.breaker import PollGapEvent
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
            ghost_duration_s=None, backoff_overlapped=False,
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
    }


def test_close_allows_a_fresh_rotate_afterward(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.close()
    assert store.service_date is None
    store.rotate(_at(2026, 7, 20))
    assert store.counts()["discrepancy_events"] == 0

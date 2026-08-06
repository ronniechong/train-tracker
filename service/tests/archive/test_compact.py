from datetime import date, datetime, timezone

import pytest

from traintracker.archive.compact import compact_partition
from traintracker.archive.schema import SCHEMA_VERSION
from traintracker.history.store import HistoryStore
from traintracker.poller.breaker import PollGapEvent
from traintracker.state.completion import TripCompletionEvent
from traintracker.state.delay_observation import DelayObservationEvent
from traintracker.state.ghost import GhostEvent
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10, mm=0):
    # 10am UTC is safely after the 3am local boundary year-round in Melbourne.
    return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)


def test_ordinary_day_round_trips_all_five_tables(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="3",
        )
    )
    store.ghost_log.record(
        GhostEvent(
            trip_id="t1", last_seen_at=_at(2026, 7, 20), last_seen_position=(-37.8, 144.9),
            reappeared_at=_at(2026, 7, 20, 10, 5), reappear_position=(-37.8, 145.0),
            loop_contained=False, ghost_duration_s=300.0, backoff_overlapped=False,
        )
    )
    store.completion_log.record(
        TripCompletionEvent(
            trip_id="t1", route_id="r1", service_date="2026-07-20",
            scheduled_terminus_arrival=_at(2026, 7, 20, 11, 0),
            actual_terminus_arrival=_at(2026, 7, 20, 11, 2),
            delay_seconds=120, status="completed",
        )
    )
    store.delay_observation_log.record(
        DelayObservationEvent(
            trip_id="t1", route_id="r1", service_date="2026-07-20",
            observed_at=_at(2026, 7, 20, 10, 30), current_delay_s=90,
            stops_remaining=3, active_alert_flag=False,
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 7, 20)))

    assert set(tables) == {
        "discrepancy_events", "ghost_events", "poll_gap_events",
        "trip_completion_events", "delay_observation_events",
    }
    assert tables["discrepancy_events"].num_rows == 1
    assert tables["ghost_events"].num_rows == 1
    assert tables["poll_gap_events"].num_rows == 0
    assert tables["trip_completion_events"].num_rows == 1
    assert tables["delay_observation_events"].num_rows == 1

    row = tables["discrepancy_events"].to_pylist()[0]
    assert row["trip_id"] == "t1"
    assert row["service_date"] == date(2026, 7, 20)
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["is_gap_marker"] is False


def test_gap_day_produces_marker_rows_in_every_table_but_poll_gap_events(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.gap_log.record(
        PollGapEvent(
            started_at=_at(2026, 7, 20, 2, 0), ended_at=_at(2026, 7, 20, 2, 30),
            reason="circuit_breaker", consecutive_failures=5, max_level_reached_s=60.0,
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 7, 20)))

    assert tables["poll_gap_events"].num_rows == 1
    for name in (
        "discrepancy_events", "ghost_events",
        "trip_completion_events", "delay_observation_events",
    ):
        rows = tables[name].to_pylist()
        assert len(rows) == 1
        marker = rows[0]
        assert marker["is_gap_marker"] is True
        assert marker["gap_started_at"] == _at(2026, 7, 20, 2, 0)
        assert marker["gap_ended_at"] == _at(2026, 7, 20, 2, 30)
        assert marker["gap_reason"] == "circuit_breaker"
        assert marker["service_date"] == date(2026, 7, 20)
        # every real-data column is null on a marker row
        real_columns = set(rows[0]) - {
            "schema_version", "service_date", "is_gap_marker",
            "gap_started_at", "gap_ended_at", "gap_reason",
        }
        assert all(marker[c] is None for c in real_columns)


def test_dst_transition_day_compacts_without_ambiguity(tmp_path):
    # 2026-10-04 is the AEDT spring-forward date (see gtfstime tests) --
    # 2-3am local doesn't exist that day, but this partition's own rotate()
    # boundary sits comfortably after it (10am UTC), so this only proves
    # the compaction path is unaffected by the transition, not that the
    # boundary math itself is DST-safe (that's `gtfstime.py`'s own tests).
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 10, 4))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="dst-1", observed_at=_at(2026, 10, 4), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="1",
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 10, 4)))

    assert tables["discrepancy_events"].num_rows == 1
    row = tables["discrepancy_events"].to_pylist()[0]
    assert row["service_date"] == date(2026, 10, 4)
    assert row["observed_at"] == _at(2026, 10, 4)


def test_missing_table_in_an_old_partition_is_treated_as_empty(tmp_path):
    # Simulates a partition file predating a table's introduction (same
    # scenario `store.py.read_completion_events` already handles) -- drop
    # `delay_observation_events` from a fresh partition entirely.
    import sqlite3

    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.close()
    path = store.partition_path(date(2026, 7, 20))
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE delay_observation_events")
    conn.commit()
    conn.close()

    tables = compact_partition(path)
    assert tables["delay_observation_events"].num_rows == 0

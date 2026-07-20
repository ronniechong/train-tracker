import pytest

from traintracker.gtfs.joinrate import RealtimeTripRef, compute_join_rate


def test_join_rate_matches_m1_methodology_shape():
    # Mirrors spike/FINDINGS.md Q5: SCHEDULED trips almost all join; ADDED
    # trips are real-time-only and would unfairly depress the ratio if
    # counted in the primary (scheduled-only) denominator.
    snapshot_ids = frozenset({"SCHED_1", "SCHED_2", "SCHED_3"})
    realtime = [
        RealtimeTripRef("SCHED_1", "SCHEDULED"),
        RealtimeTripRef("SCHED_2", "SCHEDULED"),
        RealtimeTripRef("SCHED_UNMATCHED", "SCHEDULED"),  # not in snapshot
        RealtimeTripRef("RT_ONLY_1", "ADDED"),  # never expected to join
        RealtimeTripRef("RT_ONLY_2", "ADDED"),
    ]

    result = compute_join_rate(realtime, snapshot_ids)

    assert result.total_scheduled == 3
    assert result.matched_scheduled == 2
    assert result.join_pct_scheduled_only == pytest.approx(2 / 3 * 100)
    assert result.unmatched_scheduled_trip_ids == frozenset({"SCHED_UNMATCHED"})

    # join_pct_all counts everything, including the ADDED trips that can
    # never match — so it's lower than the scheduled-only ratio.
    assert result.total_all == 5
    assert result.matched_all == 2
    assert result.join_pct_all < result.join_pct_scheduled_only


def test_join_rate_100_percent_when_all_scheduled_trips_match():
    snapshot_ids = frozenset({"A", "B"})
    realtime = [
        RealtimeTripRef("A", "SCHEDULED"),
        RealtimeTripRef("B", "SCHEDULED"),
    ]
    result = compute_join_rate(realtime, snapshot_ids)
    assert result.join_pct_scheduled_only == 100.0


def test_join_rate_with_no_realtime_trips_is_vacuously_100_percent():
    result = compute_join_rate([], frozenset({"A"}))
    assert result.join_pct_scheduled_only == 100.0
    assert result.join_pct_all == 100.0

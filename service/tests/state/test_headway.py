from datetime import date, datetime, timedelta, timezone

from traintracker.state.headway import (
    MIN_ARRIVALS_FOR_GAP_DETECTION,
    HeadwayTracker,
    compute_headway_info,
)
from traintracker.state.merge import StopTimeUpdate, TrainSnapshot


def _at(offset_s: float) -> datetime:
    return datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _direction_lookup(known: dict[str, int] | None = None):
    known = {"t1": 0} if known is None else known

    def lookup(trip_id: str, service_date: date) -> int | None:
        return known.get(trip_id)
    return lookup


def _stu(stop_sequence, stop_id, arrival_time=None, departure_time=None) -> StopTimeUpdate:
    return StopTimeUpdate(
        stop_sequence=stop_sequence, stop_id=stop_id,
        arrival_delay=None, arrival_time=arrival_time,
        departure_delay=None, departure_time=departure_time,
        schedule_relationship=None,
    )


def _snapshot_at_stop(
    now: datetime, stop_id="PLAT_A", trip_id="t1", route_id="2-BEG", start_date="20260720",
) -> TrainSnapshot:
    """A two-anchor rolling window (current stop + its successor) that
    makes `derive_station_state` report "at" `stop_id` right at `now` --
    dwelling from `now - 10s` to `now + 60s`. A single-anchor window can't
    be used here: `derive_station_state`'s cur/nxt loop only recognizes a
    genuine mid-dwell "at" when a successor anchor is present too."""
    epoch = int(now.timestamp())
    return TrainSnapshot(
        trip_id=trip_id, route_id=route_id, start_time="08:00:00", start_date=start_date,
        schedule_relationship="SCHEDULED",
        stop_time_updates=(
            _stu(2, stop_id, arrival_time=epoch - 10, departure_time=epoch + 60),
            _stu(3, "PLAT_NEXT", arrival_time=epoch + 500, departure_time=epoch + 560),
        ),
        schedule_updated_at=now,
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )


def _snapshot_between(now: datetime, trip_id="t1", route_id="2-BEG", start_date="20260720") -> TrainSnapshot:
    """Departed the previous stop, heading toward one far in the future --
    `derive_station_state` reports "between", not "at"."""
    epoch = int(now.timestamp())
    return TrainSnapshot(
        trip_id=trip_id, route_id=route_id, start_time="08:00:00", start_date=start_date,
        schedule_relationship="SCHEDULED",
        stop_time_updates=(
            _stu(2, "PLAT_A", arrival_time=epoch - 100, departure_time=epoch - 60),
            _stu(3, "PLAT_NEXT", arrival_time=epoch + 500, departure_time=epoch + 560),
        ),
        schedule_updated_at=now,
        latitude=None, longitude=None, bearing=None, position_updated_at=None,
    )


# --- compute_headway_info (pure function) -----------------------------

def test_empty_buffer_is_all_null():
    info = compute_headway_info(__import__("collections").deque(), _at(0))
    assert info.average_headway_seconds is None
    assert info.sample_size == 0
    assert info.seconds_since_last_arrival is None
    assert info.gap_detected is False


def test_single_arrival_has_no_average_but_has_seconds_since():
    from collections import deque
    arrivals = deque([_at(0)])
    info = compute_headway_info(arrivals, _at(30))
    assert info.average_headway_seconds is None
    assert info.sample_size == 1
    assert info.seconds_since_last_arrival == 30
    assert info.gap_detected is False


def test_two_arrivals_average_but_never_gap_detected_below_min_sample():
    from collections import deque
    arrivals = deque([_at(0), _at(300)])  # one gap: 300s
    # Current wait is enormous relative to the single historical gap, but
    # a single gap is too noisy to average meaningfully.
    info = compute_headway_info(arrivals, _at(300 + 5000))
    assert info.average_headway_seconds == 300
    assert info.sample_size == 2
    assert info.gap_detected is False
    assert MIN_ARRIVALS_FOR_GAP_DETECTION == 3


def test_gap_detected_once_current_wait_exceeds_twice_the_average():
    from collections import deque
    arrivals = deque([_at(0), _at(300), _at(600)])  # gaps: 300, 300 -> avg 300
    info = compute_headway_info(arrivals, _at(600 + 601))  # wait 601 > 2*300
    assert info.average_headway_seconds == 300
    assert info.sample_size == 3
    assert info.gap_detected is True


def test_gap_not_detected_when_current_wait_is_within_twice_the_average():
    from collections import deque
    arrivals = deque([_at(0), _at(300), _at(600)])
    info = compute_headway_info(arrivals, _at(600 + 599))  # wait 599 < 2*300
    assert info.gap_detected is False


# --- HeadwayTracker.tick -----------------------------------------------

def test_dwelling_across_multiple_ticks_records_only_one_arrival():
    tracker = HeadwayTracker(_direction_lookup())
    now = _at(0)

    tracker.tick({}, now - timedelta(seconds=1))  # consume the priming tick
    tracker.tick({"t1": _snapshot_at_stop(now)}, now)
    tracker.tick({"t1": _snapshot_at_stop(now + timedelta(seconds=10))}, now + timedelta(seconds=10))
    tracker.tick({"t1": _snapshot_at_stop(now + timedelta(seconds=20))}, now + timedelta(seconds=20))

    info = tracker.headway_for("PLAT_A", "2-BEG", 0, now + timedelta(seconds=20))
    assert info.sample_size == 1


def test_a_second_dwell_after_departing_records_a_second_arrival():
    tracker = HeadwayTracker(_direction_lookup())
    t0 = _at(0)

    tracker.tick({}, t0 - timedelta(seconds=1))  # consume the priming tick
    tracker.tick({"t1": _snapshot_at_stop(t0)}, t0)
    # Train departs -- no longer "at" the stop.
    tracker.tick({"t1": _snapshot_between(t0 + timedelta(seconds=100))}, t0 + timedelta(seconds=100))
    # Train returns (e.g. a later trip on the same route/direction/stop).
    t1 = t0 + timedelta(seconds=400)
    tracker.tick({"t1": _snapshot_at_stop(t1)}, t1)

    info = tracker.headway_for("PLAT_A", "2-BEG", 0, t1)
    assert info.sample_size == 2


def test_buffer_rolls_over_past_max_arrivals():
    tracker = HeadwayTracker(_direction_lookup())
    t = _at(0)
    for i in range(8):
        tracker.tick({"t1": _snapshot_at_stop(t)}, t)
        t = t + timedelta(seconds=300)
        tracker.tick({"t1": _snapshot_between(t)}, t)  # depart, clear dwell bookkeeping
        t = t + timedelta(seconds=10)

    info = tracker.headway_for("PLAT_A", "2-BEG", 0, t)
    assert info.sample_size == 6  # capped at MAX_ARRIVALS_PER_GROUP


def test_different_routes_at_the_same_stop_do_not_blend():
    tracker = HeadwayTracker(_direction_lookup({"t1": 0, "t2": 0}))
    now = _at(0)

    tracker.tick({}, now - timedelta(seconds=1))  # consume the priming tick
    tracker.tick({"t1": _snapshot_at_stop(now, stop_id="PLAT_A", trip_id="t1", route_id="2-BEG")}, now)
    tracker.tick({"t2": _snapshot_at_stop(now, stop_id="PLAT_A", trip_id="t2", route_id="3-CRB")}, now)

    beg = tracker.headway_for("PLAT_A", "2-BEG", 0, now)
    crb = tracker.headway_for("PLAT_A", "3-CRB", 0, now)
    assert beg.sample_size == 1
    assert crb.sample_size == 1


def test_different_directions_at_the_same_stop_and_route_do_not_blend():
    tracker = HeadwayTracker(_direction_lookup({"t1": 0, "t2": 1}))
    now = _at(0)

    tracker.tick({}, now - timedelta(seconds=1))  # consume the priming tick
    tracker.tick({"t1": _snapshot_at_stop(now, stop_id="PLAT_A", trip_id="t1", route_id="2-BEG")}, now)
    tracker.tick({"t2": _snapshot_at_stop(now, stop_id="PLAT_A", trip_id="t2", route_id="2-BEG")}, now)

    outbound = tracker.headway_for("PLAT_A", "2-BEG", 0, now)
    inbound = tracker.headway_for("PLAT_A", "2-BEG", 1, now)
    assert outbound.sample_size == 1
    assert inbound.sample_size == 1


def test_unknown_group_returns_all_null_info():
    tracker = HeadwayTracker(_direction_lookup())
    info = tracker.headway_for("PLAT_UNSEEN", "2-BEG", 0, _at(0))
    assert info.sample_size == 0
    assert info.average_headway_seconds is None
    assert info.gap_detected is False


def test_trip_with_no_resolvable_direction_is_never_recorded():
    tracker = HeadwayTracker(_direction_lookup(known={}))
    now = _at(0)

    tracker.tick({}, now - timedelta(seconds=1))  # consume the priming tick
    tracker.tick({"t1": _snapshot_at_stop(now)}, now)

    info = tracker.headway_for("PLAT_A", "2-BEG", 0, now)
    assert info.sample_size == 0


def test_priming_tick_does_not_record_an_already_dwelling_train():
    """Regression test (found via a live diagnostic, 2026-08-18): a
    freshly (re)started tracker's first tick must not treat trains
    already mid-dwell as fresh arrivals -- otherwise every poller
    restart manufactures a burst of near-simultaneous fake arrivals for
    whatever happens to already be dwelling at that instant."""
    tracker = HeadwayTracker(_direction_lookup())
    now = _at(0)

    tracker.tick({"t1": _snapshot_at_stop(now)}, now)  # first tick ever -- priming

    info = tracker.headway_for("PLAT_A", "2-BEG", 0, now)
    assert info.sample_size == 0

    # But the tracker DID note it's dwelling, so it won't be recorded as
    # a fresh arrival on a later tick just for still sitting there.
    tracker.tick({"t1": _snapshot_at_stop(now + timedelta(seconds=10))}, now + timedelta(seconds=10))
    info = tracker.headway_for("PLAT_A", "2-BEG", 0, now + timedelta(seconds=10))
    assert info.sample_size == 0

    # A genuinely later dwell (after departing) is recorded normally.
    t1 = now + timedelta(seconds=400)
    tracker.tick({"t1": _snapshot_between(now + timedelta(seconds=100))}, now + timedelta(seconds=100))
    tracker.tick({"t1": _snapshot_at_stop(t1)}, t1)
    info = tracker.headway_for("PLAT_A", "2-BEG", 0, t1)
    assert info.sample_size == 1

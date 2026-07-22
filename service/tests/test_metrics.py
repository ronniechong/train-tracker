from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter

from traintracker.gateway.client import Feed
from traintracker.metrics import STALENESS_THRESHOLD_S, CountingEventLog, Metrics
from traintracker.poller.breaker import CircuitBreaker
from traintracker.poller.loop import CycleResult
from traintracker.state.ghost import GhostEvent
from traintracker.state.merge import DiscrepancyEvent


class _FakeEventLog:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _discrepancy(trip_id="t1"):
    return DiscrepancyEvent(
        trip_id=trip_id, observed_at=datetime.now(timezone.utc),
        discrepancy_type="vp_without_tu", tu_value=None, vp_value="2",
    )


def _ghost(loop_contained: bool):
    return GhostEvent(
        trip_id="t1", last_seen_at=None, last_seen_position=None,
        reappeared_at=None, reappear_position=None, loop_contained=loop_contained,
        ghost_duration_s=None, backoff_overlapped=False,
    )


def test_counting_event_log_increments_and_delegates():
    registry = CollectorRegistry()
    counter = Counter("test_events_total", "test", registry=registry)
    inner = _FakeEventLog()
    log = CountingEventLog(inner, counter)

    event = _discrepancy()
    log.record(event)

    assert registry.get_sample_value("test_events_total") == 1.0
    assert inner.events == [event]


def test_counting_event_log_applies_label_fn():
    registry = CollectorRegistry()
    counter = Counter("test_ghost_total", "test", ["loop_contained"], registry=registry)
    inner = _FakeEventLog()
    log = CountingEventLog(inner, counter, lambda e: {"loop_contained": str(e.loop_contained).lower()})

    log.record(_ghost(loop_contained=True))
    log.record(_ghost(loop_contained=False))
    log.record(_ghost(loop_contained=True))

    assert registry.get_sample_value("test_ghost_total", {"loop_contained": "true"}) == 2.0
    assert registry.get_sample_value("test_ghost_total", {"loop_contained": "false"}) == 1.0
    assert len(inner.events) == 3


def test_event_logs_wraps_all_three_and_still_persists():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    discrepancy_inner = _FakeEventLog()
    ghost_inner = _FakeEventLog()
    gap_inner = _FakeEventLog()

    discrepancy_log, ghost_log, gap_log = metrics.event_logs(
        discrepancy_inner, ghost_inner, gap_inner,
    )
    discrepancy_log.record(_discrepancy())
    ghost_log.record(_ghost(loop_contained=True))

    assert registry.get_sample_value("traintracker_discrepancy_events_total") == 1.0
    assert registry.get_sample_value(
        "traintracker_ghost_events_total", {"loop_contained": "true"}
    ) == 1.0
    assert discrepancy_inner.events and ghost_inner.events
    assert gap_inner.events == []  # untouched, nothing recorded on it yet


def test_record_cycle_sets_result_counter_and_gauges():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    breaker = CircuitBreaker()
    result = CycleResult(ok=True, changed_feeds=frozenset(), lowest_remaining=42)

    metrics.record_cycle(result, breaker)

    assert registry.get_sample_value("traintracker_poll_cycles_total", {"result": "ok"}) == 1.0
    assert registry.get_sample_value("traintracker_rate_limit_remaining") == 42.0
    assert registry.get_sample_value("traintracker_backoff_active") == 0.0


def test_record_cycle_marks_backoff_active_when_breaker_is_escalated():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    breaker = CircuitBreaker()
    breaker.record_failure(datetime.now(timezone.utc))
    result = CycleResult(ok=False, changed_feeds=frozenset(), lowest_remaining=None)

    metrics.record_cycle(result, breaker)

    assert registry.get_sample_value("traintracker_poll_cycles_total", {"result": "error"}) == 1.0
    assert registry.get_sample_value("traintracker_backoff_active") == 1.0
    # No throttle window reported this cycle -- gauge is left at its default (0),
    # not fabricated from nothing.
    assert registry.get_sample_value("traintracker_rate_limit_remaining") == 0.0


def test_record_feed_ages_only_sets_gauge_for_feeds_seen_at_least_once():
    registry = CollectorRegistry()
    metrics = Metrics(registry)
    seen_at = datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc)

    def last_changed_at(feed):
        return seen_at if feed == Feed.TRIP_UPDATES else None

    metrics.record_feed_ages((Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS), last_changed_at)

    assert registry.get_sample_value(
        "traintracker_feed_last_changed_timestamp_seconds", {"feed": Feed.TRIP_UPDATES.value}
    ) == seen_at.timestamp()
    assert registry.get_sample_value(
        "traintracker_feed_last_changed_timestamp_seconds", {"feed": Feed.VEHICLE_POSITIONS.value}
    ) is None


def test_staleness_threshold_matches_the_settled_decision():
    assert STALENESS_THRESHOLD_S == 300

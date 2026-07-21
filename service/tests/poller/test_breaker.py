import random
from datetime import datetime, timedelta, timezone

from traintracker.poller.breaker import CircuitBreaker, LADDER_S, RATE_LIMIT_LOW_WATERMARK

T0 = datetime(2026, 7, 21, 0, 0, 0, tzinfo=timezone.utc)


def _breaker() -> CircuitBreaker:
    return CircuitBreaker(rng=random.Random(0))


def test_starts_at_base_interval_not_backing_off():
    breaker = _breaker()
    assert breaker.backoff_active is False
    assert LADDER_S[0] * 0.8 <= breaker.next_interval() <= LADDER_S[0] * 1.2


def test_escalates_one_level_per_consecutive_failure():
    breaker = _breaker()
    breaker.record_failure(T0)
    assert breaker.backoff_active is True
    assert LADDER_S[1] * 0.8 <= breaker.next_interval() <= LADDER_S[1] * 1.2

    breaker.record_failure(T0 + timedelta(seconds=30))
    assert LADDER_S[2] * 0.8 <= breaker.next_interval() <= LADDER_S[2] * 1.2


def test_escalation_caps_at_top_of_ladder():
    breaker = _breaker()
    for i in range(10):
        breaker.record_failure(T0 + timedelta(seconds=i))
    assert LADDER_S[-1] * 0.8 <= breaker.next_interval() <= LADDER_S[-1] * 1.2


def test_low_rate_limit_remaining_escalates_even_on_success():
    breaker = _breaker()
    gap = breaker.record_success(T0, remaining=RATE_LIMIT_LOW_WATERMARK)
    assert breaker.backoff_active is True
    assert gap is None  # escalating, not recovering -- no episode to report yet


def test_healthy_success_does_not_escalate():
    breaker = _breaker()
    gap = breaker.record_success(T0, remaining=1000)
    assert breaker.backoff_active is False
    assert gap is None


def test_recovery_emits_one_gap_episode_with_reason_code():
    breaker = _breaker()
    breaker.record_failure(T0)
    breaker.record_failure(T0 + timedelta(seconds=15))
    gap = breaker.record_success(T0 + timedelta(seconds=45), remaining=1000)

    assert gap is not None
    assert gap.reason == "circuit_breaker"
    assert gap.started_at == T0
    assert gap.ended_at == T0 + timedelta(seconds=45)
    assert gap.consecutive_failures == 2
    assert breaker.backoff_active is False


def test_only_one_episode_recorded_across_a_multi_tick_backoff():
    # Regression guard for the same over-logging shape 2d's discrepancy log
    # had to edge-trigger against: a gap row per escalated tick, not per
    # episode, would flood the log during a long backoff.
    breaker = _breaker()
    for i in range(5):
        assert breaker.record_success(T0 + timedelta(seconds=i), remaining=2) is None
    gap = breaker.record_success(T0 + timedelta(seconds=100), remaining=1000)
    assert gap is not None
    assert gap.consecutive_failures == 0  # this episode was rate-limit-driven, not failure-driven

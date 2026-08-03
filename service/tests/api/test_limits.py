import pytest

from traintracker.api.limits import RateLimiter, RateLimitExceeded


def test_per_ip_cap_trips_after_max_requests():
    limiter = RateLimiter(max_per_ip=2, max_global=100, window_s=60)

    limiter.check("1.1.1.1", "state", now=0.0)
    limiter.check("1.1.1.1", "state", now=1.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.1.1.1", "state", now=2.0)


def test_different_ips_tracked_independently():
    limiter = RateLimiter(max_per_ip=1, max_global=100, window_s=60)

    limiter.check("1.1.1.1", "state", now=0.0)
    limiter.check("2.2.2.2", "state", now=0.0)  # does not raise


def test_global_cap_trips_even_when_spread_across_many_ips():
    limiter = RateLimiter(max_per_ip=100, max_global=2, window_s=60)

    limiter.check("1.1.1.1", "state", now=0.0)
    limiter.check("2.2.2.2", "state", now=1.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check("3.3.3.3", "state", now=2.0)


def test_window_resets_after_it_elapses():
    limiter = RateLimiter(max_per_ip=1, max_global=100, window_s=60)

    limiter.check("1.1.1.1", "state", now=0.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.1.1.1", "state", now=1.0)

    limiter.check("1.1.1.1", "state", now=61.0)  # new window -- does not raise


def test_stale_per_ip_entries_are_pruned_after_two_windows_idle():
    limiter = RateLimiter(max_per_ip=1, max_global=100, window_s=60)

    limiter.check("1.1.1.1", "state", now=0.0)
    assert "1.1.1.1" in limiter._per_ip

    # A different IP's later hits eventually trigger a sweep (at most once
    # per window) once 1.1.1.1 has been idle past 2x the window.
    limiter.check("2.2.2.2", "state", now=60.0)
    limiter.check("2.2.2.2", "state", now=121.0)

    assert "1.1.1.1" not in limiter._per_ip
    assert "1.1.1.1" not in limiter._last_seen

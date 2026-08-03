"""SSE connection caps (M3 finding #4 + #9) and REST rate limiting: both
sized to protect the whole shared deployment host -- this container isn't
the only thing running there -- not just this project's own availability.
First-cut values, not tuned against real traffic; revisit once this is
actually deployed and used.
"""

from __future__ import annotations

from prometheus_client import Counter

MAX_GLOBAL_SSE_CONNECTIONS = 50
MAX_PER_IP_SSE_CONNECTIONS = 5


class ConnectionLimitExceeded(Exception):
    pass


class ConnectionTracker:
    def __init__(
        self,
        max_global: int = MAX_GLOBAL_SSE_CONNECTIONS,
        max_per_ip: int = MAX_PER_IP_SSE_CONNECTIONS,
    ):
        self._max_global = max_global
        self._max_per_ip = max_per_ip
        self._global_count = 0
        self._per_ip: dict[str, int] = {}

    def acquire(self, client_ip: str) -> None:
        if self._global_count >= self._max_global:
            raise ConnectionLimitExceeded("global SSE connection cap reached")
        if self._per_ip.get(client_ip, 0) >= self._max_per_ip:
            raise ConnectionLimitExceeded("per-IP SSE connection cap reached")
        self._global_count += 1
        self._per_ip[client_ip] = self._per_ip.get(client_ip, 0) + 1

    def release(self, client_ip: str) -> None:
        self._global_count = max(0, self._global_count - 1)
        remaining = self._per_ip.get(client_ip, 0) - 1
        if remaining <= 0:
            self._per_ip.pop(client_ip, None)
        else:
            self._per_ip[client_ip] = remaining


RATE_LIMIT_WINDOW_S = 60.0
MAX_REQUESTS_PER_WINDOW_PER_IP = 120
# Protects the shared host even if a flood is spread across many source
# IPs, none of which individually trips the per-IP cap above -- the
# scenario the per-IP-only design couldn't catch.
MAX_REQUESTS_PER_WINDOW_GLOBAL = 600

# Labelled by which cap tripped ("per_ip" vs "global") so the two failure
# shapes -- one noisy client vs. a distributed flood -- stay distinguishable
# in Grafana rather than collapsing into one undifferentiated count.
RATE_LIMITED_REQUESTS_TOTAL = Counter(
    "traintracker_rate_limited_requests_total",
    "REST requests rejected by the rate limiter, by endpoint and which cap tripped",
    ["endpoint", "scope"],
)


class RateLimitExceeded(Exception):
    pass


class _FixedWindowCounter:
    """A single (window_start, count) pair that resets once the window
    elapses. Fixed-window, not a token bucket or sliding log -- same
    simplicity tradeoff as ConnectionTracker above: good enough as a
    first-cut abuse signal, not built for billing-grade precision (a
    client can burst up to 2x the nominal rate right at a window
    boundary; acceptable here).
    """

    def __init__(self, window_s: float):
        self._window_s = window_s
        self._window_start = 0.0
        self._count = 0

    def hit(self, now: float) -> int:
        if now - self._window_start >= self._window_s:
            self._window_start = now
            self._count = 0
        self._count += 1
        return self._count


class RateLimiter:
    """Per-IP AND global fixed-window request caps, in-process -- mirrors
    ConnectionTracker's per-IP+global pair above, extended here to also
    catch a flood spread across many distinct IPs, not just one noisy
    client. Matches the single-worker constraint the same way
    ConnectionTracker does: no shared store needed, exactly one process
    ever runs.

    M7 P1: `_per_ip` is pruned of entries idle past 2x the window on every
    call, capped to run at most once per window (`_last_sweep`) so a busy
    process doesn't pay an O(n) scan on every single request -- fixes the
    unbounded-growth gap noted here previously (one entry per unique IP
    ever seen, for the life of the process; same shape as the ghost
    tracker's gap, see JOURNAL). A window-old entry is, by definition,
    already back to a fresh count on its next hit, so dropping it loses no
    real rate-limit state.
    """

    def __init__(
        self,
        max_per_ip: int = MAX_REQUESTS_PER_WINDOW_PER_IP,
        max_global: int = MAX_REQUESTS_PER_WINDOW_GLOBAL,
        window_s: float = RATE_LIMIT_WINDOW_S,
    ):
        self._max_per_ip = max_per_ip
        self._max_global = max_global
        self._window_s = window_s
        self._global = _FixedWindowCounter(window_s)
        self._per_ip: dict[str, _FixedWindowCounter] = {}
        self._last_seen: dict[str, float] = {}
        self._last_sweep = 0.0

    def _sweep_stale(self, now: float) -> None:
        if now - self._last_sweep < self._window_s:
            return
        self._last_sweep = now
        stale_after = 2 * self._window_s
        stale_ips = [ip for ip, last in self._last_seen.items() if now - last >= stale_after]
        for ip in stale_ips:
            self._per_ip.pop(ip, None)
            self._last_seen.pop(ip, None)

    def check(self, client_ip: str, endpoint: str, now: float) -> None:
        self._sweep_stale(now)
        self._last_seen[client_ip] = now
        global_count = self._global.hit(now)
        ip_count = self._per_ip.setdefault(client_ip, _FixedWindowCounter(self._window_s)).hit(now)

        if global_count > self._max_global:
            RATE_LIMITED_REQUESTS_TOTAL.labels(endpoint=endpoint, scope="global").inc()
            raise RateLimitExceeded("global rate limit exceeded")
        if ip_count > self._max_per_ip:
            RATE_LIMITED_REQUESTS_TOTAL.labels(endpoint=endpoint, scope="per_ip").inc()
            raise RateLimitExceeded("per-IP rate limit exceeded")

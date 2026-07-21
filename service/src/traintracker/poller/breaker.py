"""Circuit breaker for the poll loop: 10s -> 30s -> 60s -> 5min ladder,
jittered, driven by consecutive failures AND the `x-rate-limit` remaining
count (CLAUDE.md's settled poll-interval decision + M2 spec-review finding
#6/#7). Escalating on a low remaining count, not just on outright failures,
is what makes this a *polite* consumer rather than one that only backs off
after already tripping the real rate limit.

`backoff_active` is the same reason-code contract 2d's ghost tracker and
`StateStore.ingest()` already consume — this breaker is the one thing that
sets it, nothing new to build on that side.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

LADDER_S: tuple[float, ...] = (10.0, 30.0, 60.0, 300.0)
JITTER_FRACTION = 0.2

# A throttle window's `remaining` count at or below this is treated as
# pressure to back off proactively, same as a soft failure, rather than
# waiting to actually get a 429.
RATE_LIMIT_LOW_WATERMARK = 5


@dataclass(frozen=True)
class PollGapEvent:
    """One row per backoff *episode* (escalate -> eventually recover), not
    one row per tick -- matches 2d's edge-triggering precedent for exactly
    the same reason (StateStore's discrepancy log)."""

    started_at: datetime
    ended_at: datetime
    reason: str  # "circuit_breaker" -- the only reason code this milestone defines
    consecutive_failures: int
    max_level_reached_s: float


class CircuitBreaker:
    def __init__(
        self,
        ladder: tuple[float, ...] = LADDER_S,
        low_watermark: int = RATE_LIMIT_LOW_WATERMARK,
        rng: random.Random | None = None,
    ):
        self._ladder = ladder
        self._low_watermark = low_watermark
        self._rng = rng or random.Random()
        self._level = 0
        self._consecutive_failures = 0
        self._episode_started_at: datetime | None = None
        self._max_level_reached = 0

    @property
    def backoff_active(self) -> bool:
        return self._level > 0

    def next_interval(self) -> float:
        base = self._ladder[self._level]
        jitter = base * JITTER_FRACTION
        return base + self._rng.uniform(-jitter, jitter)

    def record_failure(self, now: datetime) -> None:
        self._consecutive_failures += 1
        self._escalate(now)

    def record_success(self, now: datetime, remaining: int | None) -> PollGapEvent | None:
        """`remaining` is the lowest `x-rate-limit` remaining count seen this
        cycle across throttle windows, or None if the feed sent none (SA)."""
        rate_limited = remaining is not None and remaining <= self._low_watermark
        if rate_limited:
            self._escalate(now)
            return None
        return self._recover(now)

    def _escalate(self, now: datetime) -> None:
        if self._level == 0:
            self._episode_started_at = now
        self._level = min(self._level + 1, len(self._ladder) - 1)
        self._max_level_reached = max(self._max_level_reached, self._level)

    def _recover(self, now: datetime) -> PollGapEvent | None:
        if self._level == 0:
            return None
        episode = PollGapEvent(
            started_at=self._episode_started_at,
            ended_at=now,
            reason="circuit_breaker",
            consecutive_failures=self._consecutive_failures,
            max_level_reached_s=self._ladder[self._max_level_reached],
        )
        self._level = 0
        self._episode_started_at = None
        self._max_level_reached = 0
        self._consecutive_failures = 0
        return episode

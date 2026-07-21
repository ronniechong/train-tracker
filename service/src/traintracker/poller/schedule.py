"""Service-hours-aware poll cadence (CLAUDE.md's settled poll-interval
decision + M2 spec-review finding #7: overnight slowdown must be
schedule/calendar-hour-based, not entity-count-based).

The overnight window (00:00-07:00 Australia/Melbourne) is M1's own measured
"overnight" band (`spike/analyze.py`'s `Q2_BANDS`) -- real measured data
(33-83% coverage by band, 6.2% zero-entity within this exact window), not
an arbitrary guess. GTFS `calendar.txt` only tells you which *day* a
service pattern runs, not clock hours -- true per-day operating-hour
boundaries would need `stop_times.txt`, which nothing in this codebase
parses yet. A fixed, measured window is the right scope for this
milestone; revisit only if that measured band turns out to be wrong, not
as a placeholder for "the real thing" later.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

BASE_INTERVAL_S = 10.0
BASE_JITTER_FRACTION = 0.3

OVERNIGHT_START_HOUR = 0
OVERNIGHT_END_HOUR = 7
OVERNIGHT_INTERVAL_RANGE_S = (30.0, 60.0)


def is_overnight(now_utc: datetime, tz: ZoneInfo = MELBOURNE_TZ) -> bool:
    """`now_utc` must be timezone-aware."""
    local_hour = now_utc.astimezone(tz).hour
    return OVERNIGHT_START_HOUR <= local_hour < OVERNIGHT_END_HOUR


def base_interval(
    now_utc: datetime,
    tz: ZoneInfo = MELBOURNE_TZ,
    rng: random.Random | None = None,
) -> float:
    """The "normal" (non-breaker-escalated) cadence for this moment: 10s +/-
    jitter during service hours, a slower 30-60s window overnight."""
    rng = rng or random.Random()
    if is_overnight(now_utc, tz):
        lo, hi = OVERNIGHT_INTERVAL_RANGE_S
        return rng.uniform(lo, hi)
    jitter = BASE_INTERVAL_S * BASE_JITTER_FRACTION
    return BASE_INTERVAL_S + rng.uniform(-jitter, jitter)

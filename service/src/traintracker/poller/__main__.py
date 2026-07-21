"""The real poll loop: `python -m traintracker.poller`.

Runs forever (until SIGINT/SIGTERM) at a service-hours-aware, breaker-backed
cadence. 2a's `python -m traintracker.gateway` one-shot smoke check remains
available separately for manual auth diagnostics.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

from ..gateway.client import API_KEY_ENV, GatewayClient
from ..redaction import configure_logging
from ..state.eventlog import InMemoryEventLog
from ..state.store import StateStore
from .healthcheck import PING_URL_ENV
from .loop import PollerLoop

logger = logging.getLogger("traintracker.poller")

# Docker's default stop grace period is 10s before SIGKILL. A single
# `time.sleep(interval)` could block for up to the breaker's 5-minute cap
# (or the overnight 30-60s window), so a SIGTERM mid-sleep would get force-
# killed rather than shut down cleanly. Sleep in small slices instead and
# recheck the stop flag between them.
SHUTDOWN_CHECK_INTERVAL_S = 1.0

# Discrepancy/ghost/gap events are recorded (2d, 2b) but never printed
# anywhere -- they live only in the running process's memory, invisible to
# anyone reviewing a burn-in after the fact via `docker compose logs`
# (nothing queries them until 2e's real persistence + M3's API exist).
# This is a deliberate stopgap for that gap, not 2e done early: a periodic
# cumulative-count line, reading `.events` directly off the concrete
# InMemoryEventLog instances `main()` already holds, not through the
# `EventLog` Protocol (which doesn't guarantee `.events` at all).
SUMMARY_INTERVAL_S = 3600.0


def _interruptible_sleep(loop: PollerLoop, seconds: float) -> None:
    remaining = seconds
    while remaining > 0 and not loop.stopped:
        time.sleep(min(SHUTDOWN_CHECK_INTERVAL_S, remaining))
        remaining -= SHUTDOWN_CHECK_INTERVAL_S


def main() -> int:
    # The dead-man ping URL carries its own secret as a path segment (not a
    # header, like the API key) -- httpx's own request logging prints full
    # URLs at INFO level, so without registering it here it leaks straight
    # into logs on every successful cycle. Caught live 2026-07-21: the real
    # URL appeared in a docker compose logs capture during 2b verification.
    configure_logging(
        os.environ.get(API_KEY_ENV, ""),
        os.environ.get(PING_URL_ENV, ""),
        level=logging.INFO,
    )

    # InMemoryEventLog for now: 2e ("history writer") swaps in a
    # SQLite-backed EventLog later without anything here needing to change
    # (see state/eventlog.py's own docstring) -- these events are lost on
    # restart until then, which is expected at this milestone.
    discrepancy_log = InMemoryEventLog()
    ghost_log = InMemoryEventLog()
    gap_log = InMemoryEventLog()
    store = StateStore(discrepancy_log=discrepancy_log, ghost_log=ghost_log)

    loop = PollerLoop(gateway=GatewayClient(), store=store, gap_log=gap_log)

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("received signal %d, shutting down after this cycle", signum)
        loop.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("poller starting")
    last_summary_at = datetime.now(timezone.utc)
    while not loop.stopped:
        cycle_start = datetime.now(timezone.utc)
        result = loop.run_cycle(cycle_start)
        interval = loop.next_interval(cycle_start)
        logger.info(
            "cycle ok=%s changed=%s backoff_active=%s next_in=%.1fs",
            result.ok,
            sorted(f.value for f in result.changed_feeds),
            loop.breaker.backoff_active,
            interval,
        )

        if (cycle_start - last_summary_at).total_seconds() >= SUMMARY_INTERVAL_S:
            logger.info(
                "hourly summary: discrepancies=%d ghost_episodes=%d breaker_gap_episodes=%d",
                len(discrepancy_log.events),
                len(ghost_log.events),
                len(gap_log.events),
            )
            last_summary_at = cycle_start

        _interruptible_sleep(loop, interval)

    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

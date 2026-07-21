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
from .loop import PollerLoop

logger = logging.getLogger("traintracker.poller")

# Docker's default stop grace period is 10s before SIGKILL. A single
# `time.sleep(interval)` could block for up to the breaker's 5-minute cap
# (or the overnight 30-60s window), so a SIGTERM mid-sleep would get force-
# killed rather than shut down cleanly. Sleep in small slices instead and
# recheck the stop flag between them.
SHUTDOWN_CHECK_INTERVAL_S = 1.0


def _interruptible_sleep(loop: PollerLoop, seconds: float) -> None:
    remaining = seconds
    while remaining > 0 and not loop.stopped:
        time.sleep(min(SHUTDOWN_CHECK_INTERVAL_S, remaining))
        remaining -= SHUTDOWN_CHECK_INTERVAL_S


def main() -> int:
    configure_logging(os.environ.get(API_KEY_ENV, ""), level=logging.INFO)

    # InMemoryEventLog for now: 2e ("history writer") swaps in a
    # SQLite-backed EventLog later without anything here needing to change
    # (see state/eventlog.py's own docstring) -- these events are lost on
    # restart until then, which is expected at this milestone.
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    gap_log = InMemoryEventLog()

    loop = PollerLoop(gateway=GatewayClient(), store=store, gap_log=gap_log)

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("received signal %d, shutting down after this cycle", signum)
        loop.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("poller starting")
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
        _interruptible_sleep(loop, interval)

    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

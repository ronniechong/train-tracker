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
from pathlib import Path

from prometheus_client import start_http_server

from ..gateway.client import API_KEY_ENV, GatewayClient
from ..gtfs.pinning import PinManifest
from ..history.store import HistoryStore
from ..metrics import Metrics
from ..redaction import configure_logging
from ..state.store import StateStore
from .healthcheck import PING_URL_ENV
from .loop import ALL_FEEDS, PollerLoop

# Fixed container-internal mount point (see Dockerfile's `VOLUME` and
# `deploy/docker-compose.yml`) -- `TT_DATA_DIR` only exists as a compose-level
# bind-mount substitution on the host side, never as an env var in-container.
DATA_DIR = Path("/data")

# Scraped by Prometheus over the `internal` docker network (2f) -- not
# published to the host, so this doesn't change the poller's external
# exposure at all. Not the OpenTelemetry-default 9464 or node_exporter's
# 9100, just a value distinct from both to avoid any confusion reading logs.
METRICS_PORT = 9109

logger = logging.getLogger("traintracker.poller")

# Docker's default stop grace period is 10s before SIGKILL. A single
# `time.sleep(interval)` could block for up to the breaker's 5-minute cap
# (or the overnight 30-60s window), so a SIGTERM mid-sleep would get force-
# killed rather than shut down cleanly. Sleep in small slices instead and
# recheck the stop flag between them.
SHUTDOWN_CHECK_INTERVAL_S = 1.0

# Periodic summary line for anyone reviewing a burn-in via `docker compose
# logs` -- counts are read via `HistoryStore.counts()`, i.e. today's
# service_date partition, not a process-lifetime cumulative total (2b's
# original stopgap, before 2e's persistence existed, counted from process
# start; that in-memory counter is gone now that events survive a restart).
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

    # 2e: day-partitioned SQLite persistence for discrepancy/ghost/gap
    # events, paired with whichever static snapshot digest (2c) is pinned to
    # each service_date. `history.rotate(now)` (called once per cycle below)
    # is what routes each `.record(event)` call to the right day's file --
    # merge.py/ghost.py/breaker.py stay unaware partitioning exists at all.
    history = HistoryStore(
        history_dir=DATA_DIR / "history",
        pin_manifest=PinManifest(DATA_DIR / "gtfs" / "pin_manifest.json"),
    )

    # 2f: wrap 2e's persisting EventLogs with counting, same composable
    # pattern -- each `.record(event)` call now both increments a Prometheus
    # counter AND persists to SQLite, still with no changes needed to
    # merge.py/ghost.py/breaker.py.
    metrics = Metrics()
    start_http_server(METRICS_PORT)
    discrepancy_log, ghost_log, gap_log = metrics.event_logs(
        history.discrepancy_log, history.ghost_log, history.gap_log,
    )
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
        history.rotate(cycle_start)
        result = loop.run_cycle(cycle_start)
        metrics.record_cycle(result, loop.breaker)
        metrics.record_feed_ages(ALL_FEEDS, loop.last_changed_at)
        interval = loop.next_interval(cycle_start)
        logger.info(
            "cycle ok=%s changed=%s backoff_active=%s next_in=%.1fs",
            result.ok,
            sorted(f.value for f in result.changed_feeds),
            loop.breaker.backoff_active,
            interval,
        )

        if (cycle_start - last_summary_at).total_seconds() >= SUMMARY_INTERVAL_S:
            counts = history.counts()
            logger.info(
                "hourly summary (service_date=%s): discrepancies=%d ghost_episodes=%d "
                "breaker_gap_episodes=%d",
                history.service_date,
                counts.get("discrepancy_events", 0),
                counts.get("ghost_events", 0),
                counts.get("poll_gap_events", 0),
            )
            last_summary_at = cycle_start

        _interruptible_sleep(loop, interval)

    history.close()
    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

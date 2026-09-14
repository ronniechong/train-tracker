"""The V/Line poll loop: `python -m traintracker.vline_poller`.

A separate process from `traintracker.poller` (M10 R1 — blast-radius
isolation, not shared-process-with-a-mode-flag) with its own smaller API
app (M10 R2, `vline_poller.app`). Deliberately minimal for this first
slice: no history/archive persistence, no static-GTFS join, no trip-
completion tracking yet — those are separate, explicitly follow-up tasks
(see `milestones/10-vline-regional-trains.md`'s Phase B section in the
private repo), not silently skipped. What this DOES do — poll, decode,
merge, ghost/coasting, serve state + SSE — is exactly what Phase A
validated works identically to Metro's already-solved feeds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

import httpx
import uvicorn
from prometheus_client import start_http_server

from ..gateway.client import API_KEY_ENV, Feed, GatewayClient
from ..metrics import Metrics
from ..poller import healthcheck
from ..poller.loop import PollerLoop
from ..redaction import configure_logging
from ..state.eventhub import InProcessEventHub
from ..state.eventlog import InMemoryEventLog
from ..state.store import StateStore
from .app import create_vline_app

logger = logging.getLogger("traintracker.vline_poller")

# Confirmed live (artifacts/vline-feed-probe.md, private repo) — same
# gateway host/auth family as Metro, distinct dataset resource.
VLINE_BASE_URL_ENV = "TT_VLINE_API_BASE_URL"
DEFAULT_VLINE_BASE_URL = (
    "https://api.opendata.transport.vic.gov.au/opendata/public-transport"
    "/gtfs/realtime/v1/vline"
)

# V/Line has no Service Alerts (M10 R5) — only these two feeds exist for
# this poller to fetch, ever.
VLINE_FEEDS: tuple[Feed, ...] = (Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS)

# Distinct dead-man's-switch target from Metro's `TT_DEADMAN_PING_URL` --
# a V/Line-specific outage must page as "V/Line poller down", not get
# silently absorbed into Metro's own already-configured check.
VLINE_PING_URL_ENV = "TT_VLINE_DEADMAN_PING_URL"

# Distinct from Metro poller's 8000/9109 -- both processes may run on the
# same host's shared `internal`/`monitoring` networks, so these must not
# collide.
API_PORT = 8100
METRICS_PORT = 9110

SHUTDOWN_CHECK_INTERVAL_S = 1.0


async def _interruptible_sleep(loop: PollerLoop, seconds: float) -> None:
    remaining = seconds
    while remaining > 0 and not loop.stopped:
        await asyncio.sleep(min(SHUTDOWN_CHECK_INTERVAL_S, remaining))
        remaining -= SHUTDOWN_CHECK_INTERVAL_S


async def main() -> int:
    configure_logging(
        os.environ.get(API_KEY_ENV, ""),
        os.environ.get(VLINE_PING_URL_ENV, ""),
        level=logging.INFO,
    )

    metrics = Metrics()
    start_http_server(METRICS_PORT)

    # In-memory only for this first slice -- no HistoryStore/persistence
    # yet (needed before the archiver, R7, can pick this data up; tracked
    # as a separate follow-up task, not forgotten).
    store = StateStore(
        discrepancy_log=InMemoryEventLog(),
        ghost_log=InMemoryEventLog(),
        on_tick=metrics.record_tracked_trips,
        # completion_tracker intentionally None: TripCompletionTracker's
        # (mode, distance_category) threshold refactor (Gate 4) hasn't
        # landed yet -- V/Line trips simply aren't completion-tracked
        # until it does, same "optional feature, honest absence" pattern
        # StateStore already supports for Metro's own tests.
    )
    gap_log = InMemoryEventLog()

    gateway = GatewayClient(
        base_url_override=os.environ.get(VLINE_BASE_URL_ENV, DEFAULT_VLINE_BASE_URL)
    )
    loop = PollerLoop(gateway=gateway, store=store, gap_log=gap_log, feeds=VLINE_FEEDS)

    hub = InProcessEventHub()
    api = create_vline_app(loop=loop, store=store, hub=hub, metrics=metrics)
    server = uvicorn.Server(uvicorn.Config(api, host="0.0.0.0", port=API_PORT, log_level="info"))

    def handle_signal() -> None:
        logger.info("received stop signal, shutting down after this cycle")
        loop.stop()
        server.should_exit = True

    event_loop = asyncio.get_running_loop()
    event_loop.add_signal_handler(signal.SIGINT, handle_signal)
    event_loop.add_signal_handler(signal.SIGTERM, handle_signal)

    healthcheck_client = httpx.AsyncClient()

    async def _run_poll_loop() -> None:
        logger.info("vline poller starting")
        while not loop.stopped:
            cycle_start = datetime.now(timezone.utc)
            result = await loop.run_cycle(cycle_start)
            metrics.record_cycle(result, loop.breaker)
            metrics.record_feed_ages(VLINE_FEEDS, loop.last_changed_at)
            hub.publish(cycle_start)
            if result.ok:
                await healthcheck.ping(healthcheck_client, os.environ.get(VLINE_PING_URL_ENV))

            interval = loop.next_interval(cycle_start)
            logger.info(
                "vline cycle ok=%s changed=%s backoff_active=%s next_in=%.1fs",
                result.ok,
                sorted(f.value for f in result.changed_feeds),
                loop.breaker.backoff_active,
                interval,
            )
            await _interruptible_sleep(loop, interval)

        server.should_exit = True

    logger.info("vline poller+api starting (api on :%d, internal to the ingress network only)", API_PORT)
    # See traintracker.poller.__main__ for why `_serve()` not `serve()` --
    # same signal-handler-conflict reasoning applies identically here.
    await asyncio.gather(_run_poll_loop(), server._serve())

    await gateway.aclose()
    await healthcheck_client.aclose()
    logger.info("vline poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

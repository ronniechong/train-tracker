"""The V/Line poll loop: `python -m traintracker.vline_poller`.

A separate process from `traintracker.poller`, with its own API/SSE app,
own day-partitioned history, and own static-GTFS join -- isolates V/Line
ingestion from Metro's so a fault in one can't affect the other.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from prometheus_client import start_http_server

from ..gateway.client import API_KEY_ENV, Feed, GatewayClient
from ..gtfs.pinning import PinManifest
from ..gtfs.schedule_cache import PinnedScheduleCache
from ..history.store import HistoryStore
from ..metrics import Metrics
from ..poller import healthcheck
from ..poller.loop import PollerLoop
from ..redaction import configure_logging
from ..state.completion import DistanceCategory, TripCompletionTracker
from ..state.eventhub import InProcessEventHub
from ..state.store import StateStore
from .app import create_vline_app

# PTV's official V/Line long-distance corridors -- everything else is
# "short" for punctuality purposes.
LONG_DISTANCE_ROUTE_NAME_HINTS = (
    "warrnambool", "albury", "shepparton", "swan hill", "echuca", "bairnsdale",
)

DATA_DIR = Path("/data")

logger = logging.getLogger("traintracker.vline_poller")

VLINE_BASE_URL_ENV = "TT_VLINE_API_BASE_URL"
DEFAULT_VLINE_BASE_URL = (
    "https://api.opendata.transport.vic.gov.au/opendata/public-transport"
    "/gtfs/realtime/v1/vline"
)

# No Service Alerts feed for V/Line.
VLINE_FEEDS: tuple[Feed, ...] = (Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS)

VLINE_PING_URL_ENV = "TT_VLINE_DEADMAN_PING_URL"

# Distinct from the Metro poller's 8000/9109.
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

    gtfs_dir = DATA_DIR / "gtfs"
    pin_manifest = PinManifest(gtfs_dir / "pin_manifest.json")
    schedule_cache = PinnedScheduleCache(gtfs_dir, pin_manifest)

    def _distance_category(trip_id: str, service_date: date) -> DistanceCategory | None:
        route = schedule_cache.route_for(trip_id, service_date)
        if route is None:
            return None
        name = route.long_name.lower()
        return "long" if any(h in name for h in LONG_DISTANCE_ROUTE_NAME_HINTS) else "short"

    history = HistoryStore(history_dir=DATA_DIR / "history", pin_manifest=pin_manifest)
    discrepancy_log, ghost_log, gap_log, completion_log, _delay_observation_log = metrics.event_logs(
        history.discrepancy_log, history.ghost_log, history.gap_log,
        history.completion_log, history.delay_observation_log,
    )
    completion_tracker = TripCompletionTracker(
        completion_log, schedule_cache.terminus_for,
        mode="vline", distance_category_lookup=_distance_category,
    )
    store = StateStore(
        discrepancy_log=discrepancy_log,
        ghost_log=ghost_log,
        on_tick=metrics.record_tracked_trips,
        completion_tracker=completion_tracker,
    )

    gateway = GatewayClient(
        base_url_override=os.environ.get(VLINE_BASE_URL_ENV, DEFAULT_VLINE_BASE_URL)
    )
    loop = PollerLoop(gateway=gateway, store=store, gap_log=gap_log, feeds=VLINE_FEEDS)

    hub = InProcessEventHub()
    api = create_vline_app(loop=loop, store=store, hub=hub, metrics=metrics, schedule_cache=schedule_cache)
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
            history.rotate(cycle_start)
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
    # `_serve()`, not `serve()` -- see traintracker.poller.__main__ for why.
    await asyncio.gather(_run_poll_loop(), server._serve())

    await gateway.aclose()
    await healthcheck_client.aclose()
    history.close()
    logger.info("vline poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

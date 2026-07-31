"""The real poll loop: `python -m traintracker.poller`.

Runs forever (until SIGINT/SIGTERM) at a service-hours-aware, breaker-backed
cadence. 2a's `python -m traintracker.gateway` one-shot smoke check remains
available separately for manual auth diagnostics.

Async since M3 (2026-07-30): this loop now shares a process and an asyncio
event loop with the FastAPI/SSE server (CLAUDE.md's M3 process-boundary
decision — same process, so 2d's in-process `EventHub` can be read directly
without any IPC). `GatewayClient`/`healthcheck.ping` converted to
`httpx.AsyncClient` alongside this; `CircuitBreaker`/`HistoryStore` have no
actual I/O latency worth yielding on (pure computation / small local SQLite
writes) and stay synchronous, called directly from this async loop. The
FastAPI app is run here too, as `uvicorn.Server.serve()` embedded directly
in this event loop rather than via `uvicorn`'s own CLI/multiprocess runner
-- that's what makes the "single worker, always" constraint (M3 finding #3)
automatic rather than something that needs enforcing: there is no
`--workers` flag to misconfigure in this mode, only one process ever exists.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from prometheus_client import start_http_server

from ..api.app import create_app
from ..gateway.client import API_KEY_ENV, GatewayClient
from ..gtfs.pinning import PinManifest
from ..gtfs.schedule_cache import PinnedScheduleCache
from ..history.store import HistoryStore
from ..metrics import Metrics
from ..redaction import configure_logging
from ..state.eventhub import InProcessEventHub
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

# M3: the FastAPI app. Bound to all interfaces *inside the container* --
# not the host -- because reachability is enforced by Docker network
# membership, not by binding to loopback: this container only carries the
# new `ingress` network (shared solely with the `caddy` service) alongside
# its existing, untouched `internal`/egress-restricted network, so nothing
# outside `ingress` can reach this port regardless of which interface it
# binds. Binding to literal 127.0.0.1 here would make it unreachable from
# the separate `caddy` container entirely, which isn't what invariant #4
# ("API binds localhost") was written to prevent -- that invariant's intent
# (only Caddy can reach it) is what's preserved; the mechanism had to change
# once Caddy became a second container rather than a same-machine process.
API_PORT = 8000

logger = logging.getLogger("traintracker.poller")

# Docker's default stop grace period is 10s before SIGKILL. A single
# `asyncio.sleep(interval)` could block for up to the breaker's 5-minute cap
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


async def _interruptible_sleep(loop: PollerLoop, seconds: float) -> None:
    remaining = seconds
    while remaining > 0 and not loop.stopped:
        await asyncio.sleep(min(SHUTDOWN_CHECK_INTERVAL_S, remaining))
        remaining -= SHUTDOWN_CHECK_INTERVAL_S


async def main() -> int:
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
    gtfs_dir = DATA_DIR / "gtfs"
    history = HistoryStore(
        history_dir=DATA_DIR / "history",
        pin_manifest=PinManifest(gtfs_dir / "pin_manifest.json"),
    )

    # Station-schedule feature: its own PinManifest instance over the same
    # file -- cheap and stateless (loads/saves the whole JSON per call), so
    # a second instance is simpler than threading HistoryStore's through.
    schedule_cache = PinnedScheduleCache(gtfs_dir, PinManifest(gtfs_dir / "pin_manifest.json"))

    # 2f: wrap 2e's persisting EventLogs with counting, same composable
    # pattern -- each `.record(event)` call now both increments a Prometheus
    # counter AND persists to SQLite, still with no changes needed to
    # merge.py/ghost.py/breaker.py.
    metrics = Metrics()
    start_http_server(METRICS_PORT)
    discrepancy_log, ghost_log, gap_log = metrics.event_logs(
        history.discrepancy_log, history.ghost_log, history.gap_log,
    )
    store = StateStore(
        discrepancy_log=discrepancy_log, ghost_log=ghost_log, on_tick=metrics.record_tracked_trips,
    )

    gateway = GatewayClient()
    loop = PollerLoop(gateway=gateway, store=store, gap_log=gap_log)

    # M3: producer side of 2d's EventHub interface finally gets a
    # consumer (the SSE route below) -- one hub instance, shared between
    # the poll loop (publishes) and the API (subscribes).
    hub = InProcessEventHub()
    api = create_app(loop=loop, store=store, hub=hub, schedule_cache=schedule_cache)
    server = uvicorn.Server(uvicorn.Config(api, host="0.0.0.0", port=API_PORT, log_level="info"))

    def handle_signal() -> None:
        logger.info("received stop signal, shutting down after this cycle")
        loop.stop()
        server.should_exit = True

    # `loop.add_signal_handler` (not `signal.signal`) so the handler runs on
    # the event loop rather than possibly interrupting an in-flight await --
    # standard asyncio practice, and fine here since the target is always
    # Linux/Docker (SIGTERM is what `docker stop` sends).
    event_loop = asyncio.get_running_loop()
    event_loop.add_signal_handler(signal.SIGINT, handle_signal)
    event_loop.add_signal_handler(signal.SIGTERM, handle_signal)

    async def _run_poll_loop() -> None:
        logger.info("poller starting")
        last_summary_at = datetime.now(timezone.utc)
        while not loop.stopped:
            cycle_start = datetime.now(timezone.utc)
            history.rotate(cycle_start)
            result = await loop.run_cycle(cycle_start)
            metrics.record_cycle(result, loop.breaker)
            metrics.record_feed_ages(ALL_FEEDS, loop.last_changed_at)
            # M3 finding #5's resolution: one tick per completed cycle,
            # whatever the current cadence actually is (10s or the
            # overnight 30-60s) -- SSE consumers see reality, not an
            # invented fixed cadence. The value itself carries no data
            # (see state/eventhub.py); every subscriber recomputes state
            # fresh from `loop`/`store` when it wakes up.
            hub.publish(cycle_start)
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

            await _interruptible_sleep(loop, interval)

        # The poll loop stopping (e.g. a signal) is also the API's cue to
        # stop, and vice versa -- `asyncio.gather` below waits on both, so
        # either one exiting first must signal the other rather than
        # leaving `gather` waiting forever on a server no signal handler
        # told to stop (only SIGINT/SIGTERM set `server.should_exit` above).
        server.should_exit = True

    logger.info("poller+api starting (api on :%d, internal to the ingress network only)", API_PORT)
    # Deliberately `server._serve()`, not the public `server.serve()`:
    # `serve()` wraps everything in `capture_signals()`, which calls plain
    # `signal.signal()` for SIGINT/SIGTERM -- that would silently replace
    # the `event_loop.add_signal_handler` registration above the moment the
    # server started, breaking the poll-loop/API shared-shutdown coordination
    # this function depends on. `_serve()` is the same coroutine minus that
    # wrapper (verified against uvicorn 0.52.0's source; re-check this if
    # uvicorn's version bound in pyproject.toml ever moves).
    await asyncio.gather(_run_poll_loop(), server._serve())

    await gateway.aclose()
    history.close()
    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

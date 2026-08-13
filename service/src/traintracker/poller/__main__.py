"""The real poll loop: `python -m traintracker.poller`.

Runs forever (until SIGINT/SIGTERM) at a service-hours-aware, breaker-backed
cadence. `python -m traintracker.gateway`'s one-shot smoke check remains
available separately for manual auth diagnostics.

This loop shares a process and an asyncio event loop with the FastAPI/SSE
server, so the in-process `EventHub` can be read directly without any IPC.
`GatewayClient`/`healthcheck.ping` use `httpx.AsyncClient`;
`CircuitBreaker`/`HistoryStore` have no actual I/O latency worth yielding on
(pure computation / small local SQLite writes) and stay synchronous, called
directly from this async loop. The FastAPI app is run here too, as
`uvicorn.Server.serve()` embedded directly in this event loop rather than
via `uvicorn`'s own CLI/multiprocess runner -- that's what makes the
"single worker, always" constraint automatic rather than something that
needs enforcing: there is no `--workers` flag to misconfigure in this
mode, only one process ever exists.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from prometheus_client import start_http_server

from ..ai.budget import BudgetEnforcedLLMClient, BudgetTracker
from ..ai.llm_client import ANTHROPIC_API_KEY_ENV, AnthropicLLMClient, LLMClient
from ..ai.tools import ToolContext
from ..ai.tracing import LangfuseTracedLLMClient
from ..ai.weekly_digest import aggregate_weekly_stats, compose_weekly_digest
from ..api.app import BRIEFING_TOKEN_ENV, create_app
from ..digests.store import LineStat, WeeklyDigestRecord, WeeklyDigestStore
from ..gateway.client import API_KEY_ENV, GatewayClient
from ..gtfs.pinning import PinManifest
from ..gtfs.schedule_cache import NoPinnedSnapshotError, PinnedScheduleCache
from ..history.store import HistoryStore
from ..insights.store import InsightsStore
from ..metrics import Metrics
from ..redaction import configure_logging
from ..state.completion import TripCompletionTracker
from ..state.delay_observation import DelayObservationTracker
from ..state.eventhub import InProcessEventHub
from ..state.store import StateStore
from .healthcheck import PING_URL_ENV
from .insights_trigger import InsightsTrigger, run_insights_cycle
from .loop import ALL_FEEDS, PollerLoop
from .slack import WEBHOOK_URL_ENV, post_message
from .weekly_digest_trigger import WeeklyDigestTrigger

# Fixed container-internal mount point (see Dockerfile's `VOLUME` and
# `deploy/docker-compose.yml`) -- `TT_DATA_DIR` only exists as a compose-level
# bind-mount substitution on the host side, never as an env var in-container.
DATA_DIR = Path("/data")

# Read-only mount of the archiver's own persistent state dir (see
# `deploy/docker-compose.yml`'s `archiver` service and `TT_ARCHIVE_STATE_DIR`)
# -- added so the API can read `public_status.json`, the one fact from the
# otherwise fully internal HF archive pipeline (M9) worth showing on the
# public site. `poller` never writes here; the archiver container is the
# only writer, same as `DATA_DIR`'s roles are reversed for that mount.
ARCHIVE_STATE_DIR = Path("/archive-state")

# Scraped by Prometheus over the `internal` docker network -- not
# published to the host, so this doesn't change the poller's external
# exposure at all. Not the OpenTelemetry-default 9464 or node_exporter's
# 9100, just a value distinct from both to avoid any confusion reading logs.
METRICS_PORT = 9109

# The FastAPI app. Bound to all interfaces *inside the container* --
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


async def _maybe_send_weekly_digest(
    trigger: WeeklyDigestTrigger,
    history: HistoryStore,
    schedule_cache: PinnedScheduleCache,
    digest_store: WeeklyDigestStore,
    ai_client: LLMClient,
    notify_client: httpx.AsyncClient,
    now: datetime,
) -> None:
    """Checked once per poll cycle (cheap: a JSON-file read behind
    `should_fire`) -- fires at most once per Monday-8am-Melbourne boundary.
    Never lets a digest failure affect the poll loop it runs inside, same
    discipline `poller/slack.py`'s `post_message` already follows for
    Slack outages specifically; this wraps the whole generation path.

    Crash-safety ordering: `trigger.mark_fired()` is the LAST thing this
    function does, only after both the Slack post attempt and the
    `WeeklyDigestStore` write have completed -- a crash or exception
    anywhere before that point leaves the boundary unmarked, so the next
    cycle simply retries rather than silently losing the week.
    """
    boundary = trigger.should_fire(now)
    if boundary is None:
        return

    week_start = boundary - timedelta(days=7)
    week_end = boundary - timedelta(days=1)
    service_dates = [week_start + timedelta(days=i) for i in range(7)]

    try:
        window = history.read_completion_events(service_dates)
        stats = aggregate_weekly_stats(window, week_start, week_end)
        try:
            routes = schedule_cache.routes_for(now)
        except NoPinnedSnapshotError:
            # No static snapshot pinned yet -- degrade to raw route_ids in
            # the narrative rather than skip the digest entirely over a
            # cosmetic naming gap.
            routes = {}
        narrative = await compose_weekly_digest(ai_client, stats, routes)
    except Exception:
        logger.exception("weekly digest generation failed, will retry next cycle")
        return

    sent = await post_message(notify_client, narrative)

    record = WeeklyDigestRecord(
        week_start=week_start,
        week_end=week_end,
        days_covered=stats.days_covered,
        on_time_count=stats.on_time_count,
        late_count=stats.late_count,
        cancelled_count=stats.cancelled_count,
        on_time_pct=stats.on_time_pct,
        narrative=narrative,
        slack_delivered=sent,
        line_stats=tuple(
            LineStat(
                route_id=line.route_id,
                trip_count=line.trip_count,
                on_time_count=line.on_time_count,
                late_count=line.late_count,
                cancelled_count=line.cancelled_count,
                on_time_pct=line.on_time_pct,
            )
            for line in stats.line_stats
        ),
    )
    try:
        digest_store.record(record)
    except Exception:
        logger.exception(
            "weekly digest DB write failed after Slack delivery attempt "
            "(sent=%s) -- will retry next cycle, may double-post", sent,
        )
        return

    trigger.mark_fired(boundary)
    logger.info(
        "weekly digest sent for %s to %s (days_covered=%d, on_time_pct=%.1f, slack_delivered=%s)",
        week_start, week_end, stats.days_covered, stats.on_time_pct, sent,
    )


async def main() -> int:
    # The dead-man ping URL carries its own secret as a path segment (not a
    # header, like the API key) -- httpx's own request logging prints full
    # URLs at INFO level, so without registering it here it would leak
    # straight into logs on every successful cycle.
    #
    # Same bug class applies to the Slack webhook URL
    # (`ai/briefing.py`/`weekly_digest.py`'s delivery path), which also
    # carries its secret as a URL path segment. Anthropic/Langfuse keys
    # registered too, belt-and-braces -- nothing today logs them, but
    # registering costs nothing and this filter is the only backstop if
    # that ever changes.
    configure_logging(
        os.environ.get(API_KEY_ENV, ""),
        os.environ.get(PING_URL_ENV, ""),
        os.environ.get(WEBHOOK_URL_ENV, ""),
        os.environ.get(ANTHROPIC_API_KEY_ENV, ""),
        os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        os.environ.get("LANGFUSE_SECRET_KEY", ""),
        # Same env-only + redaction-filter-registered treatment as every
        # other secret here (invariant #2).
        os.environ.get(BRIEFING_TOKEN_ENV, ""),
        level=logging.INFO,
    )

    # Day-partitioned SQLite persistence for discrepancy/ghost/gap
    # events, paired with whichever static snapshot digest is pinned to
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

    # Wrap the persisting EventLogs with counting, a composable pattern --
    # each `.record(event)` call now both increments a Prometheus counter
    # AND persists to SQLite, with no changes needed to
    # merge.py/ghost.py/breaker.py.
    metrics = Metrics()
    start_http_server(METRICS_PORT)
    discrepancy_log, ghost_log, gap_log, completion_log, delay_observation_log = metrics.event_logs(
        history.discrepancy_log, history.ghost_log, history.gap_log, history.completion_log,
        history.delay_observation_log,
    )
    # Real trip-completion tracking, chosen over a cheaper snapshot-based
    # stat -- reuses the same `PinnedScheduleCache` instance the
    # station-schedule feature already constructed above, so this needs no
    # new I/O path of its own.
    completion_tracker = TripCompletionTracker(completion_log, schedule_cache.terminus_for)
    # Delay/ETA-prediction, step one: the observation logger builds
    # training data for that feature. Same terminus lookup as
    # completion_tracker, no new I/O path here either.
    delay_observation_tracker = DelayObservationTracker(delay_observation_log, schedule_cache.terminus_for)
    store = StateStore(
        discrepancy_log=discrepancy_log, ghost_log=ghost_log, on_tick=metrics.record_tracked_trips,
        completion_tracker=completion_tracker, delay_observation_tracker=delay_observation_tracker,
    )

    gateway = GatewayClient()
    loop = PollerLoop(gateway=gateway, store=store, gap_log=gap_log)

    # Budget-then-trace wrapper order matters -- BudgetEnforcedLLMClient
    # is the INNER wrapper so its check runs immediately before the real
    # Anthropic call, and LangfuseTracedLLMClient is OUTER so a budget-
    # blocked attempt still lands as an ERROR span (useful observability
    # in its own right: "would have called the LLM here but skipped").
    # `AnthropicLLMClient()`/`Langfuse()` are both safe to construct with
    # no key present (verified live) -- they only fail at actual call
    # time, so this never turns a missing optional env var into a poller
    # crash-loop.
    budget_tracker = BudgetTracker(DATA_DIR / "ai" / "budget.db")
    # Shared inner layer -- ONE budget-enforced Anthropic client, so the
    # monthly cap is genuinely global across every AI-layer caller, not
    # tracked separately per feature. Each caller wraps it in its OWN
    # LangfuseTracedLLMClient instance with its own `name` (tracing.py's
    # own documented convention -- `name` is fixed per-instance, not
    # threaded per-call), so briefings and the weekly digest still show up
    # as distinct call sites in the Langfuse dashboard.
    budget_enforced_client = BudgetEnforcedLLMClient(AnthropicLLMClient(), budget_tracker)
    ai_client: LLMClient = LangfuseTracedLLMClient(budget_enforced_client, name="disruption-briefing")
    weekly_digest_client: LLMClient = LangfuseTracedLLMClient(budget_enforced_client, name="weekly-digest")
    tool_context = ToolContext(store=store, schedule_cache=schedule_cache)
    notify_client = httpx.AsyncClient()

    # Weekly performance digest: its own SQLite content store (indefinite
    # retention, unlike HistoryStore's 60-day cap) and its own trigger
    # sidecar, both under a digests/ subdirectory alongside ai/ and gtfs/.
    digests_dir = DATA_DIR / "digests"
    digest_store = WeeklyDigestStore(digests_dir / "weekly.db")
    digest_trigger = WeeklyDigestTrigger(digests_dir / "digest_trigger_state.json")

    # Insights rollups: built ahead of any chart UI or API route,
    # specifically so daily rollups start accumulating immediately --
    # they can't be computed retroactively once a source partition ages
    # out of the 60-day window. Indefinite retention, its own trigger
    # sidecar, same shape as the weekly digest's own pairing.
    insights_dir = DATA_DIR / "insights"
    insights_store = InsightsStore(insights_dir / "insights.db")
    insights_trigger = InsightsTrigger(insights_dir / "insights_trigger_state.json")

    # Producer side of the EventHub interface, consumed by the SSE route
    # below -- one hub instance, shared between the poll loop (publishes)
    # and the API (subscribes).
    hub = InProcessEventHub()
    # Briefings are on-demand only (POST /briefing/trigger, cost control --
    # replacing automatic per-cycle triggering). The AI stack built above is
    # handed to the API instead of driven from this poll loop.
    api = create_app(
        loop=loop, store=store, hub=hub, schedule_cache=schedule_cache,
        ai_client=ai_client, ai_tool_context=tool_context, ai_notify_client=notify_client,
        metrics=metrics, digest_store=digest_store, insights_store=insights_store,
        briefing_token=os.environ.get(BRIEFING_TOKEN_ENV) or None,
        archive_status_path=ARCHIVE_STATE_DIR / "public_status.json",
    )
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
            # One tick per completed cycle, whatever the current cadence
            # actually is (10s or the overnight 30-60s) -- SSE consumers
            # see reality, not an invented fixed cadence. The value itself
            # carries no data (see state/eventhub.py); every subscriber
            # recomputes state fresh from `loop`/`store` when it wakes up.
            hub.publish(cycle_start)

            # Cheap check every cycle (a JSON-file read behind should_fire);
            # actually generates+delivers a digest at most once per Monday-
            # 8am-Melbourne boundary. Never allowed to affect the core
            # cycle above -- see the helper's own docstring for the crash-
            # safety ordering this depends on.
            await _maybe_send_weekly_digest(
                digest_trigger, history, schedule_cache, digest_store,
                weekly_digest_client, notify_client, cycle_start,
            )

            # Insights rollups: also cheap when neither the finalize-
            # yesterday nor refresh-today path is due (a JSON-file read
            # each). Requires a pinned static snapshot -- unlike the
            # weekly digest's narration-only degrade-to-raw-route-ids
            # fallback, Insights' -R correction (never merge replacement-
            # bus completions into a real line) depends on
            # `route_short_name` to tell the two apart. Skipping entirely
            # when nothing is pinned yet is safer than aggregating with a
            # wrong split -- retried every cycle, same as any other
            # transient-dependency skip in this loop.
            try:
                insights_routes = schedule_cache.routes_for(cycle_start)
            except NoPinnedSnapshotError:
                insights_routes = None
            if insights_routes is not None:
                await run_insights_cycle(
                    insights_trigger, history, insights_store, insights_routes, cycle_start,
                )

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
                    "breaker_gap_episodes=%d trip_completions=%d delay_observations=%d",
                    history.service_date,
                    counts.get("discrepancy_events", 0),
                    counts.get("ghost_events", 0),
                    counts.get("poll_gap_events", 0),
                    counts.get("trip_completion_events", 0),
                    counts.get("delay_observation_events", 0),
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
    await notify_client.aclose()
    history.close()
    digest_store.close()
    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

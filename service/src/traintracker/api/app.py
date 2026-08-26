"""FastAPI app: read-only, GET-only, serves derived state directly out of
the same process's memory -- no new path to the upstream API (security
invariant #1), this only ever reads what the poll loop already fetched.

Docs/OpenAPI endpoints are disabled by default: this is a small, fixed,
already-documented public surface, not a browsable API product -- no
reason to publish a live schema explorer alongside it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from ..ai.briefing import compose_briefing
from ..ai.briefing_filter import has_briefable_alerts
from ..ai.budget import BudgetExceededError
from ..ai.llm_client import LLMClient
from ..ai.tools import ToolContext
from ..digests.store import WeeklyDigestStore
from ..gateway.client import Feed
from ..gtfs.gtfstime import service_date_for_instant
from ..gtfs.schedule import ScheduledDeparture
from ..gtfs.routes import Route
from ..gtfs.schedule_cache import NoPinnedSnapshotError, PinnedScheduleCache
from ..gtfs.stops import Stop
from ..insights.ranges import RANGE_NAMES, InvalidRangeError, resolve_range
from ..insights.store import InsightsStore
from ..metrics import STALENESS_THRESHOLD_S, Metrics
from ..poller.loop import ALL_FEEDS, PollerLoop
from ..poller.slack import post_message
from ..state.alerts import Alert as AlertRecord
from ..state.alerts import alerts_matching
from ..state.delay_model import DelayModel, predict_delay_seconds
from ..state.delay_observation import DelayFeatures, compute_delay_features
from ..state.eventhub import EventHub
from ..state.ghost import MAX_GHOST_AGE_S, TrackedTrainView
from ..state.station import current_stop_sequence, next_stop_and_delay
from ..state.store import StateStore
from .http_metrics import HttpMetricsMiddleware
from .limits import (
    RATE_LIMIT_WINDOW_S,
    ConnectionLimitExceeded,
    ConnectionTracker,
    RateLimitExceeded,
    RateLimiter,
)
from .schemas import (
    Alert,
    AlertActivePeriod,
    AlertInformedEntity,
    AlertsResponse,
    ArchiveStatusResponse,
    AttributionResponse,
    BriefingTriggerResponse,
    DelayPredictionResponse,
    DeltaResponse,
    FeedStatus,
    HealthResponse,
    InsightsHistogramStat,
    InsightsHourlyStat,
    InsightsLineStat,
    InsightsResponse,
    LineSummary,
    NextServiceLegResponse,
    NextServiceResponse,
    ScheduledTrain,
    StateResponse,
    StationListEntry,
    StationScheduleResponse,
    StationsResponse,
    StationSummary,
    Train,
    WeeklyDigest,
    WeeklyDigestListResponse,
    WeeklyLineStat,
)

# Both the static GTFS schedule and the realtime feeds are published by
# Vic DoT under CC BY 4.0 (confirmed live). Neither dataset page mandates
# an exact credit-line format, so this follows standard CC BY 4.0
# practice: name the source, link the license text, note the data is
# derived/processed rather than the original feed as-is.
DATA_ATTRIBUTION = AttributionResponse(
    source="Victoria Department of Transport and Planning",
    license="CC BY 4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    note=(
        "Train positions and schedule data displayed here are derived and "
        "processed from the Department's GTFS-Realtime and static GTFS "
        "feeds, not a direct copy of the original feeds."
    ),
)

logger = logging.getLogger("traintracker.api")

CORS_ORIGINS_ENV = "TT_CORS_ORIGINS"
# Shared-secret bearer token for POST /briefing/trigger, defense-in-depth
# on top of the tailnet-only network isolation (see create_app's
# briefing_token docstring above).
BRIEFING_TOKEN_ENV = "TT_BRIEFING_TOKEN"

# This is a cap on connection *idleness*, not a
# promised data-freshness cadence -- the actual delta cadence is whatever
# the poll loop's real cadence is (10s or the overnight 30-60s), since a
# tick only fires after a real poll cycle completes. This value only
# controls how long a truly quiet connection goes before a keepalive
# comment, chosen to stay comfortably under the reverse-ingress layer's
# idle-connection timeout (check the deployed value before relying on this
# if that ever changes -- exposure/ingress specifics live in ops docs, not
# this repo).
SSE_HEARTBEAT_INTERVAL_S = 20.0

# How long a delay-prediction fallback (last cycle's features, used when
# the current cycle's TU data is momentarily missing) stays usable --
# comfortably more than one poll cycle (10s + jitter) so a single-cycle
# `vp_without_tu` transient doesn't 422, but short enough a genuinely
# stale trip still falls through to the honest 422 below.
_DELAY_FEATURES_CACHE_MAX_AGE = timedelta(seconds=60)


def _cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV, "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _feed_status(loop: PollerLoop, feed: Feed, now: datetime) -> FeedStatus:
    changed_at = loop.last_changed_at(feed)
    stale = changed_at is None or (now - changed_at).total_seconds() > STALENESS_THRESHOLD_S
    return FeedStatus(last_changed_at=changed_at, stale=stale)


def _is_current(tracked: TrackedTrainView, now: datetime) -> bool:
    # `last_touched_at` (unlike `last_seen_at`) is set on every tick
    # regardless of feed, so this correctly ages out TU-only trips too --
    # `last_seen_at` alone stayed None forever for those, letting stale
    # ghosts accumulate and still pass this check (see state/ghost.py).
    # `_trains` itself now also evicts on this same age via
    # `TrainLifecycleTracker._evict_stale` (called every tick) -- this
    # check exists in addition because a request can land between ticks,
    # when the tracker hasn't had a chance to sweep yet.
    if tracked.last_touched_at is None:
        return True  # not yet ticked even once; shouldn't happen in practice
    return (now - tracked.last_touched_at).total_seconds() <= MAX_GHOST_AGE_S


def _parse_start_date(value: str) -> date:
    """TU's `trip.start_date` is GTFS-RT's own "YYYYMMDD" string -- kept as
    a small local parser matching state/completion.py's own precedent of a
    tiny local helper over cross-module coupling for a one-line format."""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _resolve_service_date(start_date: str | None, now: datetime) -> date:
    """TU's `trip.start_date` when present and parseable, else falls back
    to "today" resolved from wall-clock `now` -- shared by every static-
    schedule lookup keyed on a trip's own service_date."""
    if start_date is not None:
        try:
            return _parse_start_date(start_date)
        except (ValueError, IndexError):
            pass
    return service_date_for_instant(now)


def _trip_static_fields(
    schedule_cache: PinnedScheduleCache | None,
    trip_id: str,
    start_date: str | None,
    now: datetime,
) -> tuple[str | None, int | None]:
    """(trip_headsign, direction_id) from the static schedule, or (None,
    None) when unavailable (no schedule_cache configured, no snapshot
    pinned yet for the trip's service_date, or a real-time-only ADDED trip
    with no static trips.txt row) -- never an error, same "missing data is
    honest, not a crash" convention as terminus_for."""
    if schedule_cache is None:
        return None, None
    service_date = _resolve_service_date(start_date, now)
    trip = schedule_cache.trip_for(trip_id, service_date)
    if trip is None:
        return None, None
    return (trip.trip_headsign or None), trip.direction_id


def _train(
    store: StateStore,
    tracked: TrackedTrainView,
    schedule_cache: PinnedScheduleCache | None,
    now: datetime,
    stops: dict[str, Stop] | None = None,
) -> Train:
    snapshot = store.latest_snapshots.get(tracked.trip_id)
    if snapshot is not None:
        trip_headsign, direction_id = _trip_static_fields(
            schedule_cache, tracked.trip_id, snapshot.start_date, now
        )
        next_stop_id, next_stop_delay_seconds = next_stop_and_delay(snapshot, now)
        next_stop_name = (
            stops[next_stop_id].name
            if stops is not None and next_stop_id in stops
            else None
        )
        progress_stop_sequence = current_stop_sequence(snapshot, now)
        progress_total_stops = None
        terminus = None
        if progress_stop_sequence is not None and schedule_cache is not None:
            service_date = _resolve_service_date(snapshot.start_date, now)
            terminus = schedule_cache.terminus_for(tracked.trip_id, service_date)
            if terminus is not None:
                progress_total_stops = terminus.stop_sequence
        if terminus is None:
            # Symmetric with the schema's own "both null together" contract
            # -- a current position without a known total isn't useful, so
            # don't surface a half-answer (covers: no schedule_cache
            # configured, real-time-only ADDED trip with no static row, no
            # anchor surfaced yet).
            progress_stop_sequence = None
        skipped_stop_count = (
            schedule_cache.skip_stop_count_for(
                tracked.trip_id, _resolve_service_date(snapshot.start_date, now)
            )
            if schedule_cache is not None
            else None
        )
        return Train(
            trip_id=tracked.trip_id,
            route_id=snapshot.route_id,
            status=tracked.status,
            latitude=snapshot.latitude,
            longitude=snapshot.longitude,
            bearing=snapshot.bearing,
            position_updated_at=snapshot.position_updated_at,
            schedule_updated_at=snapshot.schedule_updated_at,
            last_seen_at=tracked.last_seen_at,
            start_time=snapshot.start_time,
            trip_headsign=trip_headsign,
            direction_id=direction_id,
            next_stop_id=next_stop_id,
            next_stop_name=next_stop_name,
            next_stop_delay_seconds=next_stop_delay_seconds,
            progress_stop_sequence=progress_stop_sequence,
            progress_total_stops=progress_total_stops,
            skipped_stop_count=skipped_stop_count,
        )

    # Dropped out of both live feeds entirely (coasting/ghost with only a
    # last-known fix) -- report what's actually known (position, honestly
    # timestamped via last_seen_at) rather than either inventing fresher
    # data or silently omitting the train from the response. Static fields
    # still resolvable from trip_id alone via "today"'s pin (no start_date
    # to anchor to once the live snapshot is gone). No live snapshot means
    # no stop_time_updates either, so next-stop is unresolvable the same
    # honest way `next_stop_and_delay` reports "window hasn't surfaced it".
    trip_headsign, direction_id = _trip_static_fields(schedule_cache, tracked.trip_id, None, now)
    latitude, longitude = tracked.last_position or (None, None)
    return Train(
        trip_id=tracked.trip_id,
        route_id=None,
        status=tracked.status,
        latitude=latitude,
        longitude=longitude,
        bearing=None,
        position_updated_at=None,
        schedule_updated_at=None,
        last_seen_at=tracked.last_seen_at,
        start_time=None,
        trip_headsign=trip_headsign,
        direction_id=direction_id,
        next_stop_id=None,
        next_stop_name=None,
        next_stop_delay_seconds=None,
        progress_stop_sequence=None,
        progress_total_stops=None,
        skipped_stop_count=None,
    )


def _scheduled_train(
    store: StateStore, dep: ScheduledDeparture, stops: dict[str, Stop], now: datetime,
) -> ScheduledTrain:
    """Overlays a live Trip Updates prediction onto one scheduled departure,
    when this process's own StateStore happens to have one for the exact
    (trip_id, platform) right now -- reads only already-polled in-memory
    state, no upstream call (invariant #1), same as every other route."""
    predicted_time: datetime | None = None
    delay_seconds: int | None = None
    is_cancelled = False
    is_added = False
    snapshot = store.latest_snapshots.get(dep.trip_id)
    if snapshot is not None:
        is_cancelled = snapshot.schedule_relationship == "CANCELED"
        is_added = snapshot.schedule_relationship == "ADDED"
        stu = next(
            (s for s in snapshot.stop_time_updates if s.stop_id == dep.stop_id), None
        )
        if stu is not None:
            if stu.schedule_relationship == "SKIPPED":
                is_cancelled = True
            delay_seconds = (
                stu.departure_delay if stu.departure_delay is not None else stu.arrival_delay
            )
            time_epoch = stu.departure_time if stu.departure_time is not None else stu.arrival_time
            if time_epoch is not None:
                # `StopTimeUpdate.arrival_time`/`departure_time` are typed
                # `int | None` but are actually strings at runtime --
                # protobuf's JSON mapping stringifies int64 fields. Same
                # `int(...)` coercion `state/station.py`'s `_epoch()`
                # helper already applies for the same reason.
                predicted_time = datetime.fromtimestamp(int(time_epoch), tz=timezone.utc)
            elif delay_seconds is not None:
                predicted_time = dep.scheduled_time + timedelta(seconds=delay_seconds)

    # M12 #4: headway is a property of this exact (platform, route,
    # direction), not of the individual trip -- null when this departure's
    # direction_id itself is unknown (can't resolve the group), same
    # honest-null convention as every other field here.
    headway = (
        store.headway_for(dep.stop_id, dep.route_id, dep.direction_id, now)
        if dep.direction_id is not None else None
    )

    return ScheduledTrain(
        trip_id=dep.trip_id,
        route_id=dep.route_id,
        direction_id=dep.direction_id,
        headsign=dep.headsign,
        scheduled_time=dep.scheduled_time,
        predicted_time=predicted_time,
        delay_seconds=delay_seconds,
        is_live=predicted_time is not None or delay_seconds is not None,
        is_cancelled=is_cancelled,
        platform_code=stops.get(dep.stop_id).platform_code if dep.stop_id in stops else None,
        is_added=is_added,
        average_headway_seconds=headway.average_headway_seconds if headway else None,
        headway_sample_size=headway.sample_size if headway else 0,
        seconds_since_last_arrival=headway.seconds_since_last_arrival if headway else None,
        gap_detected=headway.gap_detected if headway else False,
    )


def _alert_response(
    alert: AlertRecord,
    routes: dict[str, Route],
    schedule_cache: PinnedScheduleCache | None,
    now: datetime,
) -> Alert:
    entity_route_names: dict[int, str | None] = {}
    stop_only_indices: list[int] = []
    for i, e in enumerate(alert.informed_entities):
        if e.route_id is not None:
            # Best-effort: routes.txt may be unavailable (no pinned
            # snapshot yet) or the id may be a `-R` bus-replacement variant
            # not in the map -- None here just means "line name
            # unavailable", never raises.
            entity_route_names[i] = routes[e.route_id].long_name if e.route_id in routes else None
        elif e.stop_id is not None:
            stop_only_indices.append(i)

    # Fallback for the single-trip-cancellation shape: the feed lists the
    # trip's whole stop sequence but no route_id at all -- infer the line
    # by majority vote across the listed stops (see
    # `routes_most_likely_for_stops`'s docstring for why a strict
    # intersection was too fragile). Leaves None rather than guess when no
    # line clears the majority bar.
    if stop_only_indices and schedule_cache is not None:
        stop_ids = [alert.informed_entities[i].stop_id for i in stop_only_indices]
        try:
            matched_routes = schedule_cache.routes_most_likely_for_stops(now, stop_ids)  # type: ignore[arg-type]
        except NoPinnedSnapshotError:
            matched_routes = []
        resolved = matched_routes[0].long_name if matched_routes else None
        for i in stop_only_indices:
            entity_route_names[i] = resolved

    return Alert(
        id=alert.id,
        cause=alert.cause,
        effect=alert.effect,
        header_text=alert.header_text,
        description_text=alert.description_text,
        url=alert.url,
        active_periods=[
            AlertActivePeriod(start=p.start, end=p.end) for p in alert.active_periods
        ],
        informed_entities=[
            AlertInformedEntity(
                route_id=e.route_id,
                route_name=entity_route_names.get(i),
                stop_id=e.stop_id,
                direction_id=e.direction_id,
            )
            for i, e in enumerate(alert.informed_entities)
        ],
    )


def _current_state(
    loop: PollerLoop, store: StateStore, schedule_cache: PinnedScheduleCache | None = None
) -> StateResponse:
    now = datetime.now(timezone.utc)
    stops: dict[str, Stop] | None = None
    if schedule_cache is not None:
        try:
            stops = schedule_cache.stops_for(now)
        except NoPinnedSnapshotError:
            stops = None
    return StateResponse(
        generated_at=now,
        backoff_active=loop.breaker.backoff_active,
        feeds={feed.value: _feed_status(loop, feed, now) for feed in ALL_FEEDS},
        trains=[
            _train(store, tracked, schedule_cache, now, stops)
            for tracked in store.all_tracked()
            if _is_current(tracked, now)
        ],
    )


def _client_ip(request: Request) -> str:
    # `poller` carries the host's shared `monitoring` network (for
    # Prometheus scraping) and `internal` (for its own egress calls), so
    # this header is only trustworthy for traffic that actually arrived
    # via Caddy. Caddy itself only forwards a trustworthy value here (see
    # the `trusted_proxies` config in deploy/Caddyfile) -- it trusts
    # `X-Forwarded-For` only on the loopback hop from the co-located
    # Tailscale sidecar, not from an arbitrary client. Something on the
    # `monitoring` network forging this header directly against `poller`,
    # bypassing Caddy, is a real residual gap -- explicitly accepted, not
    # closed: `monitoring` is this same host's own single-operator stack,
    # no untrusted tenants, so this is host-compromise-equivalent risk
    # rather than a genuine additional attack surface (see
    # deploy/docker-compose.yml's `monitoring` network definition for the
    # full reasoning).
    forwarded = request.headers.get("x-forwarded-for")
    resolved = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    if os.environ.get("TT_DEBUG_CLIENT_IP"):
        # One-off toggle to directly confirm what Caddy's
        # `trusted_proxies` config actually resolves per real request,
        # without permanently logging every client IP. Unset (default) =
        # no-op, same convention as this codebase's other optional features.
        logger.info("client_ip resolved=%s raw_xff=%r", resolved, forwarded)
    return resolved


def _rate_limit_dependency(rate_limiter: RateLimiter, endpoint: str) -> Callable[[Request], Awaitable[None]]:
    async def _check(request: Request) -> None:
        try:
            rate_limiter.check(_client_ip(request), endpoint, datetime.now(timezone.utc).timestamp())
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(int(RATE_LIMIT_WINDOW_S))},
            ) from exc

    return _check


def _diff(previous: dict[str, Train], current: dict[str, Train]) -> tuple[list[Train], list[str]]:
    changed = [train for trip_id, train in current.items() if previous.get(trip_id) != train]
    removed = [trip_id for trip_id in previous if trip_id not in current]
    return changed, removed


def _sse_event(event: str, body: DeltaResponse | StateResponse) -> str:
    return f"event: {event}\ndata: {body.model_dump_json()}\n\n"


async def _event_source(
    loop: PollerLoop,
    store: StateStore,
    hub: EventHub,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_interval_s: float,
    schedule_cache: PinnedScheduleCache | None = None,
) -> AsyncIterator[str]:
    """The actual SSE event logic, independent of FastAPI/Starlette/ASGI --
    `is_disconnected` is injected rather than taking a `Request` so this can
    be driven directly in tests. (Found the hard way: httpx's `ASGITransport`
    fully awaits an ASGI app to completion before returning anything at all,
    so it cannot drive an infinite generator like this one -- there's no way
    to test the real route end-to-end without a live server. Extracting this
    function is what makes the actual event logic testable at all.)

    No shared ring buffer (deliberate scope cut) -- `sent` is local to
    this one connection, diffed fresh against `store`/`loop` on every
    tick.
    """
    # Bounded to 1: this queue only ever carries "something changed, go
    # recompute" wake-ups (the value itself is never read below), so a
    # backlog beyond the single most recent tick is pure noise, never data
    # loss -- see state/eventhub.py's InProcessEventHub.
    queue = hub.subscribe(maxsize=1)
    try:
        state = _current_state(loop, store, schedule_cache)
        sent = {train.trip_id: train for train in state.trains}
        yield _sse_event("snapshot", state)

        while True:
            if await is_disconnected():
                break
            try:
                await asyncio.wait_for(queue.get(), timeout=heartbeat_interval_s)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue

            state = _current_state(loop, store, schedule_cache)
            current = {train.trip_id: train for train in state.trains}
            changed, removed = _diff(sent, current)
            if changed or removed:
                delta = DeltaResponse(generated_at=state.generated_at, changed=changed, removed=removed)
                yield _sse_event("delta", delta)
                sent = current
    finally:
        hub.unsubscribe(queue)


def create_app(
    loop: PollerLoop,
    store: StateStore,
    hub: EventHub,
    connections: ConnectionTracker | None = None,
    rate_limiter: RateLimiter | None = None,
    heartbeat_interval_s: float = SSE_HEARTBEAT_INTERVAL_S,
    schedule_cache: PinnedScheduleCache | None = None,
    # On-demand briefings replaced automatic per-cycle triggering (cost
    # control). All four None by default, same "feature not configured"
    # 503 convention `schedule_cache` already uses -- lets tests/dev
    # construct an app without wiring the whole AI stack when they don't
    # need it.
    ai_client: LLMClient | None = None,
    ai_tool_context: ToolContext | None = None,
    ai_notify_client: httpx.AsyncClient | None = None,
    metrics: Metrics | None = None,
    # Weekly digest: None by default, same "feature not configured" 503
    # convention as the AI-stack params above.
    digest_store: WeeklyDigestStore | None = None,
    # Insights: same "feature not configured" 503 convention -- lets
    # tests/dev construct an app before the aggregation job has ever run
    # without wiring a real store.
    insights_store: InsightsStore | None = None,
    # App-level defense-in-depth for /briefing/trigger, on top of
    # (not instead of) the tailnet-only network isolation chosen for this
    # route -- that isolation turned out narrower than "tailnet-only" in
    # practice (also reachable via the shared `monitoring` network and
    # `tailscale serve` to the whole tailnet), so a
    # second, independent check is worthwhile. None by default = auth not
    # enforced, same "feature not configured" convention as the params
    # above -- lets tests/dev exercise this route without wiring a token.
    briefing_token: str | None = None,
    # Path to the archiver's `public_status.json` (see
    # `archive/public_status.py`). None by default, same "feature not
    # configured" 503 convention as the other optional params above --
    # lets tests/dev construct an app without the archiver's read-only
    # mount wired up.
    archive_status_path: Path | None = None,
    # The "Am I late?" on-demand delay prediction (M5). None by default,
    # same "feature not configured" 503 convention as the other optional
    # params above -- lets tests/dev construct an app before
    # `scripts/train_delay_model.py` has ever produced a model file.
    delay_model: DelayModel | None = None,
) -> FastAPI:
    connections = connections or ConnectionTracker()
    rate_limiter = rate_limiter or RateLimiter()

    app = FastAPI(
        title="train-tracker",
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    # Added AFTER CORSMiddleware deliberately -- Starlette's add_middleware
    # makes the LAST-added layer the OUTERMOST one, so this wraps CORS too
    # and sees the true end-to-end status/latency a client experiences,
    # not just what reaches the router.
    app.add_middleware(HttpMetricsMiddleware, metrics=metrics)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Never let a stack trace or internal path reach a public response,
        # regardless of what FastAPI's own default would otherwise do.
        # Full detail stays server-side in the log.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.get(
        "/healthz",
        response_model=HealthResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "healthz"))],
    )
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/api/state",
        response_model=StateResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "state"))],
    )
    async def get_state() -> StateResponse:
        return _current_state(loop, store, schedule_cache)

    @app.get(
        "/api/alerts",
        response_model=AlertsResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "alerts"))],
    )
    async def get_alerts(route_id: str | None = None) -> AlertsResponse:
        # Reads only the in-process StateStore's latest parsed SA snapshot
        # (see state/alerts.py + poller/loop.py) -- no upstream call
        # (invariant #1), same as every other route here. `route_id` is an
        # optional coarse filter, not a precise per-trip match (see
        # AlertInformedEntity's docstring).
        now = datetime.now(timezone.utc)
        matched = alerts_matching(store.latest_alerts, now, route_id=route_id)
        try:
            routes = schedule_cache.routes_for(now) if schedule_cache is not None else {}
        except NoPinnedSnapshotError:
            # No static snapshot pinned yet -- alerts still render, just
            # without a resolved line name (same "unavailable" convention
            # as every other optional field on this response).
            routes = {}
        return AlertsResponse(
            generated_at=now,
            alerts=[_alert_response(a, routes, schedule_cache, now) for a in matched],
        )

    @app.get(
        "/attribution",
        response_model=AttributionResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "attribution"))],
    )
    async def attribution() -> AttributionResponse:
        return DATA_ATTRIBUTION

    @app.get(
        "/archive/status",
        response_model=ArchiveStatusResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "archive_status"))],
    )
    async def archive_status() -> ArchiveStatusResponse:
        # Read-only (invariant 3), and read-only in a second sense: this
        # container never writes `archive_status_path` -- the archiver
        # container is the sole writer (see `poller/__main__.py`'s
        # `ARCHIVE_STATE_DIR` comment). A missing file (feature not wired
        # up, e.g. local dev) is the existing "not configured" 503
        # convention; a *present* file whose `last_archived_date` is null
        # (archiver wired up but has never completed a pass) is a normal
        # 200 -- those are different facts, not the same error.
        if archive_status_path is None or not archive_status_path.exists():
            raise HTTPException(status_code=503, detail="archive status feature not configured")
        try:
            body = json.loads(archive_status_path.read_text())
        except (OSError, json.JSONDecodeError):
            raise HTTPException(status_code=503, detail="archive status unavailable")
        return ArchiveStatusResponse(last_archived_date=body.get("last_archived_date"))

    @app.post(
        "/briefing/trigger",
        response_model=BriefingTriggerResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "briefing_trigger"))],
    )
    async def trigger_briefing(request: Request) -> BriefingTriggerResponse:
        # Network-level isolation (deploy/Caddyfile's :8081 block +
        # `tailscale serve`, NOT the publicly-funnelled :8080) was the
        # original, deliberate choice over an app-level token. This bearer
        # check adds a layer on top, not instead of that: the route turned
        # out reachable from more than "tailnet-only" in practice (the
        # shared `monitoring` network; `tailscale serve` also exposes it to
        # the whole tailnet, not just this deployment's own callers).
        # Security invariant #1 is unaffected either way: this only ever
        # reads already-polled local state via `ai_tool_context`, same as
        # every AI-layer tool.
        if briefing_token:
            authorization = request.headers.get("authorization", "")
            scheme, _, presented = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(presented, briefing_token):
                raise HTTPException(status_code=401, detail="missing or invalid bearer token")

        if ai_client is None or ai_tool_context is None or ai_notify_client is None:
            raise HTTPException(status_code=503, detail="briefing feature not configured")

        now = datetime.now(timezone.utc)
        if not has_briefable_alerts(store, now):
            return BriefingTriggerResponse(
                sent=False, reason="no active alerts with enough route/line detail to brief"
            )

        try:
            text = await compose_briefing(ai_client, ai_tool_context)
        except BudgetExceededError as exc:
            return BriefingTriggerResponse(sent=False, reason=str(exc))
        except Exception:
            logger.exception("on-demand briefing composition failed")
            return BriefingTriggerResponse(sent=False, reason="briefing composition failed")

        sent = await post_message(ai_notify_client, text)
        if sent and metrics is not None:
            metrics.record_briefing_sent()
        return BriefingTriggerResponse(
            sent=sent, reason=None if sent else "Slack delivery failed", text=text if sent else None
        )

    @app.get(
        "/digests/weekly",
        response_model=WeeklyDigestListResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "digests_weekly"))],
    )
    async def get_weekly_digests(limit: int = 20) -> WeeklyDigestListResponse:
        # Read-only public history (invariant 3: GET-only, derived state) --
        # no tailnet isolation needed here, unlike /briefing/trigger, since
        # this never triggers spend, only serves what a poll-loop-driven
        # trigger already generated and stored.
        if digest_store is None:
            raise HTTPException(status_code=503, detail="weekly digest feature not configured")
        stored = digest_store.list_digests(limit=limit)
        return WeeklyDigestListResponse(
            digests=[
                WeeklyDigest(
                    week_start=d.record.week_start,
                    week_end=d.record.week_end,
                    days_covered=d.record.days_covered,
                    on_time_count=d.record.on_time_count,
                    late_count=d.record.late_count,
                    cancelled_count=d.record.cancelled_count,
                    on_time_pct=d.record.on_time_pct,
                    narrative=d.record.narrative,
                    slack_delivered=d.record.slack_delivered,
                    line_stats=[
                        WeeklyLineStat(
                            route_id=line.route_id,
                            trip_count=line.trip_count,
                            on_time_count=line.on_time_count,
                            late_count=line.late_count,
                            cancelled_count=line.cancelled_count,
                            on_time_pct=line.on_time_pct,
                        )
                        for line in d.record.line_stats
                    ],
                )
                for d in stored
            ]
        )

    @app.get(
        "/api/insights",
        response_model=InsightsResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "insights"))],
    )
    async def get_insights(
        # `?range=` is the nicer external query-string name; shadows the
        # `range()` builtin within this function's scope only, which is
        # harmless since nothing here calls it.
        range: str = "today",
        start: date | None = None,
        end: date | None = None,
    ) -> InsightsResponse:
        # Read-only, GET-only, derived-only (invariant 3/1) -- same as
        # every other route here; this never triggers the aggregation
        # job itself, only reads whatever it has already precomputed,
        # cached, and refreshed periodically -- not computed on-demand
        # per request.
        if insights_store is None:
            raise HTTPException(status_code=503, detail="insights feature not configured")
        try:
            resolved = resolve_range(range, datetime.now(timezone.utc), start, end)
        except InvalidRangeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{exc} (expected range=one of {RANGE_NAMES}, or range=custom with start/end)",
            ) from exc
        result = insights_store.read_range(resolved.service_dates)
        return InsightsResponse(
            range_name=resolved.range_name,
            days_covered=list(result.days_covered),
            expected_days=resolved.expected_days,
            requested_dates=list(resolved.service_dates),
            line_stats=[
                InsightsLineStat(
                    route_id=line.route_id,
                    on_time_count=line.on_time_count,
                    late_count=line.late_count,
                    cancelled_count=line.cancelled_count,
                    gap_count=line.gap_count,
                    replacement_bus_count=line.replacement_bus_count,
                )
                for line in result.line_rollups
            ],
            hourly_stats=[
                InsightsHourlyStat(
                    route_id=hourly.route_id,
                    hour_local=hourly.hour_local,
                    completion_count=hourly.completion_count,
                )
                for hourly in result.hourly_rollups
            ],
            generated_at_by_date=result.generated_at_by_date,
            daily_line_stats={
                day: [
                    InsightsLineStat(
                        route_id=line.route_id,
                        on_time_count=line.on_time_count,
                        late_count=line.late_count,
                        cancelled_count=line.cancelled_count,
                        gap_count=line.gap_count,
                        replacement_bus_count=line.replacement_bus_count,
                    )
                    for line in lines
                ]
                for day, lines in result.daily_line_rollups.items()
            },
            histogram_stats=InsightsHistogramStat(
                on_time_count=result.histogram_rollup.on_time_count,
                late_5_10_count=result.histogram_rollup.late_5_10_count,
                late_10_plus_count=result.histogram_rollup.late_10_plus_count,
                cancelled_count=result.histogram_rollup.cancelled_count,
                gap_count=result.histogram_rollup.gap_count,
            ),
        )

    @app.get(
        "/stations/{station_id}/schedule",
        response_model=StationScheduleResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "schedule"))],
    )
    async def station_schedule(station_id: str) -> StationScheduleResponse:
        # Reads only the already-pinned static snapshot (disk, refreshed
        # nightly by a separate job) and this process's own in-memory
        # StateStore -- no path to the upstream API from this request
        # (invariant #1), same as every other route here.
        if schedule_cache is None:
            raise HTTPException(status_code=503, detail="schedule feature not configured")
        now = datetime.now(timezone.utc)
        try:
            departures = schedule_cache.next_departures_for(
                station_id, now, live_snapshots=store.latest_snapshots
            )
        except NoPinnedSnapshotError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if departures is None:
            raise HTTPException(status_code=404, detail=f"unknown station_id: {station_id}")
        no_service_today = schedule_cache.lines_no_service_today(station_id, now) or []
        stops = schedule_cache.stops_for(now)
        station_stop = stops.get(station_id)
        return StationScheduleResponse(
            station_id=station_id,
            generated_at=now,
            wheelchair_boarding=station_stop.wheelchair_boarding if station_stop else None,
            departures=[_scheduled_train(store, dep, stops, now) for dep in departures],
            lines_no_service_today=[
                LineSummary(route_id=r.route_id, short_name=r.short_name, long_name=r.long_name)
                for r in no_service_today
            ],
        )

    @app.get(
        "/api/next-service",
        response_model=NextServiceResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "next_service"))],
    )
    async def api_next_service(
        from_: str = Query(alias="from"), to: str = Query(...), route: str | None = None
    ) -> NextServiceResponse:
        # Same "reads only the pinned static snapshot" invariant as every
        # other schedule-backed route -- station/route names are resolved
        # entirely against the fixed, in-memory canonical lists (never
        # interpolated into any query/eval), per M13's security review.
        if schedule_cache is None:
            raise HTTPException(status_code=503, detail="schedule feature not configured")
        now = datetime.now(timezone.utc)
        try:
            result = schedule_cache.find_next_service(from_, to, now, route=route)
        except NoPinnedSnapshotError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(
                status_code=404,
                detail={"reason": "unknown_station", "message": "from/to name didn't resolve to exactly one station"},
            )
        # Legs carry PLATFORM stop_ids (schedule.py's precision, needed to
        # match a specific stop_time row) -- resolve each back to its
        # parent station's display name for the response, rather than
        # exposing raw platform ids to callers who only asked about
        # stations by name in the first place.
        stops = schedule_cache.stops_for(now)

        def _leg_station(stop_id: str) -> StationSummary:
            stop = stops.get(stop_id)
            parent_id = stop.parent_station if stop and stop.parent_station else stop_id
            parent = stops.get(parent_id)
            return StationSummary(station_id=parent_id, name=parent.name if parent else stop_id)

        def _platform_code(stop_id: str) -> str | None:
            stop = stops.get(stop_id)
            return stop.platform_code if stop else None

        return NextServiceResponse(
            from_station=StationSummary(station_id=result.from_station.station_id, name=result.from_station.name),
            to_station=StationSummary(station_id=result.to_station.station_id, name=result.to_station.name),
            generated_at=now,
            reason=result.reason,
            legs=[
                NextServiceLegResponse(
                    trip_id=leg.trip_id,
                    route_id=leg.route_id,
                    headsign=leg.headsign,
                    from_station=_leg_station(leg.from_stop_id),
                    from_platform_code=_platform_code(leg.from_stop_id),
                    departure_time=leg.departure_time,
                    to_station=_leg_station(leg.to_stop_id),
                    to_platform_code=_platform_code(leg.to_stop_id),
                    arrival_time=leg.arrival_time,
                )
                for leg in result.legs
            ],
        )

    @app.get(
        "/api/stations",
        response_model=StationsResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "stations"))],
    )
    async def api_stations() -> StationsResponse:
        if schedule_cache is None:
            raise HTTPException(status_code=503, detail="schedule feature not configured")
        now = datetime.now(timezone.utc)
        try:
            stations = schedule_cache.stations_for(now)
        except NoPinnedSnapshotError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return StationsResponse(
            generated_at=now,
            stations=[
                StationListEntry(
                    station_id=s.station_id,
                    name=s.name,
                    routes=[
                        LineSummary(route_id=r.route_id, short_name=r.short_name, long_name=r.long_name)
                        for r in s.routes
                    ],
                )
                for s in stations
            ],
        )

    # Last-known-good delay features per trip_id, kept only to smooth a
    # single-cycle TU miss (the `vp_without_tu` transient documented in
    # state/delay_observation.py -- TU drops a trip for one poll cycle
    # while VP still has it, zeroing `stop_time_updates` for that cycle
    # only). Bounded by _DELAY_FEATURES_CACHE_MAX_AGE_S below, not by
    # trip lifetime, so a genuinely gone trip ages out on its own.
    _last_good_features: dict[str, tuple[datetime, DelayFeatures]] = {}

    @app.get(
        "/trains/{trip_id}/delay-prediction",
        response_model=DelayPredictionResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "delay_prediction"))],
    )
    async def delay_prediction(trip_id: str) -> DelayPredictionResponse:
        # Math only against already-tracked live state + the serialized
        # model file -- no path to the upstream API or to an LLM from
        # this request (invariant #1; also why this is a plain GET
        # endpoint, not routed through ai/tools.py at all).
        if delay_model is None:
            raise HTTPException(status_code=503, detail="delay prediction feature not configured")
        if schedule_cache is None:
            raise HTTPException(status_code=503, detail="schedule feature not configured")
        snapshot = store.latest_snapshots.get(trip_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"unknown or untracked trip_id: {trip_id}")
        now = datetime.now(timezone.utc)
        service_date = _resolve_service_date(snapshot.start_date, now)
        terminus = schedule_cache.terminus_for(trip_id, service_date)
        if terminus is None:
            raise HTTPException(
                status_code=422, detail="no static schedule available for this trip"
            )
        features = compute_delay_features(snapshot, terminus, now, store.latest_alerts)
        stale = False
        if features is not None:
            _last_good_features[trip_id] = (now, features)
        else:
            cached = _last_good_features.get(trip_id)
            if cached is not None and (now - cached[0]) <= _DELAY_FEATURES_CACHE_MAX_AGE:
                _, features = cached
                stale = True
        if features is None:
            raise HTTPException(
                status_code=422, detail="not enough live data to predict this trip right now"
            )
        predicted = predict_delay_seconds(delay_model, features)
        return DelayPredictionResponse(
            trip_id=trip_id,
            predicted_delay_seconds=round(predicted),
            current_delay_s=features.current_delay_s,
            stops_remaining=features.stops_remaining,
            active_alert_flag=features.active_alert_flag,
            predicted_at=now,
            stale=stale,
        )

    @app.get("/api/stream")
    async def stream(request: Request):
        client_ip = _client_ip(request)
        try:
            connections.acquire(client_ip)
        except ConnectionLimitExceeded as exc:
            return JSONResponse(status_code=503, content={"detail": str(exc)})

        async def bound_source() -> AsyncIterator[str]:
            try:
                async for chunk in _event_source(
                    loop, store, hub, request.is_disconnected, heartbeat_interval_s, schedule_cache
                ):
                    yield chunk
            finally:
                connections.release(client_ip)

        return StreamingResponse(
            bound_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Nginx-specific, harmless elsewhere -- Caddy's own
                # buffering is disabled via `flush_interval -1` on this
                # route instead (deploy/Caddyfile, not yet built).
                "X-Accel-Buffering": "no",
            },
        )

    return app

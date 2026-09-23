"""V/Line's own minimal API/SSE surface — state, stream, healthz, station
schedule, and delay prediction.

Deliberately not `api.create_app` reused wholesale: that app also serves
alerts, insights, digests, and next-service, none of which apply here (no
Service Alerts feed, no cross-line lookup for V/Line). Station schedule and
delay prediction, unlike those, have no such blocker --
`PinnedScheduleCache`/`_scheduled_train`/`compute_delay_features`/
`predict_delay_seconds` are already mode-agnostic, so both are included
here. A separate, smaller app keeps this process's serving surface
independent of Metro's -- a bug in one can't affect the other.

Generic pieces (rate limiting, connection caps, CORS, the SSE diff loop,
train/state shaping) are imported directly from `api.app`/`api.limits`
rather than duplicated, since V/Line's VP/TU shape matches Metro's.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from ..api.app import _client_ip, _cors_origins, _is_current, _resolve_service_date, _scheduled_train, _train
from ..api.http_metrics import HttpMetricsMiddleware
from ..api.limits import (
    ConnectionLimitExceeded,
    ConnectionTracker,
    RateLimitExceeded,
    RateLimiter,
)
from ..api.schemas import (
    DelayPredictionResponse,
    DeltaResponse,
    FeedStatus,
    HealthResponse,
    LineSummary,
    StateResponse,
    StationScheduleResponse,
    Train,
)
from ..gateway.client import Feed
from ..gtfs.schedule_cache import NoPinnedSnapshotError, PinnedScheduleCache
from ..metrics import STALENESS_THRESHOLD_S, Metrics
from ..poller.loop import PollerLoop
from ..state.delay_model import DelayModel, predict_delay_seconds
from ..state.delay_observation import DelayFeatures, compute_delay_features
from ..state.eventhub import EventHub
from ..state.store import StateStore

logger = logging.getLogger("traintracker.vline_poller.app")

SSE_HEARTBEAT_INTERVAL_S = 20.0

# No Service Alerts feed for V/Line.
VLINE_FEEDS: tuple[Feed, ...] = (Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS)

# Same value and purpose as api.app's own -- smooths a single-cycle TU
# miss rather than serving a hard error for a momentary feed gap.
_DELAY_FEATURES_CACHE_MAX_AGE = timedelta(seconds=60)


def _feed_status(loop: PollerLoop, feed: Feed, now: datetime) -> FeedStatus:
    changed_at = loop.last_changed_at(feed)
    stale = changed_at is None or (now - changed_at).total_seconds() > STALENESS_THRESHOLD_S
    return FeedStatus(last_changed_at=changed_at, stale=stale)


def _current_state(
    loop: PollerLoop, store: StateStore, schedule_cache: PinnedScheduleCache | None
) -> StateResponse:
    now = datetime.now(timezone.utc)
    stops = None
    if schedule_cache is not None:
        try:
            stops = schedule_cache.stops_for(now)
        except NoPinnedSnapshotError:
            stops = None
    return StateResponse(
        generated_at=now,
        backoff_active=loop.breaker.backoff_active,
        feeds={feed.value: _feed_status(loop, feed, now) for feed in VLINE_FEEDS},
        trains=[
            _train(store, tracked, schedule_cache, now, stops)
            for tracked in store.all_tracked()
            if _is_current(tracked, now)
        ],
    )


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
    schedule_cache: PinnedScheduleCache | None,
) -> AsyncIterator[str]:
    """Same shape as `api.app._event_source` — kept as its own copy (not a
    shared import) only because that function closes over `ALL_FEEDS`
    (Metro's 3-feed set); the diff/heartbeat logic itself is identical."""
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


def _rate_limit_dependency(rate_limiter: RateLimiter, endpoint: str) -> Callable[[Request], Awaitable[None]]:
    async def _check(request: Request) -> None:
        try:
            rate_limiter.check(_client_ip(request), endpoint, datetime.now(timezone.utc).timestamp())
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": "60"},
            ) from exc

    return _check


def create_vline_app(
    loop: PollerLoop,
    store: StateStore,
    hub: EventHub,
    connections: ConnectionTracker | None = None,
    rate_limiter: RateLimiter | None = None,
    heartbeat_interval_s: float = SSE_HEARTBEAT_INTERVAL_S,
    schedule_cache: PinnedScheduleCache | None = None,
    metrics: Metrics | None = None,
    delay_model: DelayModel | None = None,
) -> FastAPI:
    connections = connections or ConnectionTracker()
    rate_limiter = rate_limiter or RateLimiter()

    app = FastAPI(
        title="train-tracker-vline",
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
    app.add_middleware(HttpMetricsMiddleware, metrics=metrics)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.get(
        "/healthz",
        response_model=HealthResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "vline_healthz"))],
    )
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/api/vline/state",
        response_model=StateResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "vline_state"))],
    )
    async def get_state() -> StateResponse:
        return _current_state(loop, store, schedule_cache)

    # No /api/vline/alerts route -- V/Line has no Service Alerts feed.

    @app.get(
        "/api/vline/stations/{station_id}/schedule",
        response_model=StationScheduleResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "vline_schedule"))],
    )
    async def station_schedule(station_id: str) -> StationScheduleResponse:
        # Same shape as api.app's own station_schedule route -- kept as its
        # own copy (not a shared import) only because that route closes
        # over Metro's `schedule_cache`/`store` module-level names; the
        # logic itself (and the `_scheduled_train` helper it calls) is
        # identical and already mode-agnostic.
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

    # Same last-known-good smoothing convention as api.app's own route --
    # see its comment for why this is trip-scoped, not a shared feature.
    _last_good_features: dict[str, tuple[datetime, DelayFeatures]] = {}

    @app.get(
        "/api/vline/trains/{trip_id}/delay-prediction",
        response_model=DelayPredictionResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "vline_delay_prediction"))],
    )
    async def delay_prediction(trip_id: str) -> DelayPredictionResponse:
        # Same shape as api.app's own delay_prediction route -- kept as its
        # own copy (not a shared import) for the same reason
        # station_schedule above is: that route closes over Metro's
        # module-level state. The logic (compute_delay_features,
        # predict_delay_seconds) is identical and already mode-agnostic.
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

    @app.get("/api/vline/stream")
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
                "X-Accel-Buffering": "no",
            },
        )

    return app

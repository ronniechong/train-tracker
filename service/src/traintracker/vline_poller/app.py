"""V/Line's own minimal API/SSE surface — state + stream + healthz only.

Deliberately NOT `api.create_app` reused wholesale: that app also serves
alerts, insights, weekly digests, delay prediction, and next-service —
none of which apply to V/Line yet (no Service Alerts per M10 R5; the
other features are Metro-specific product surface this milestone never
scoped V/Line into). Building a separate, smaller app here — rather than
threading a `mode` parameter through `create_app` — is what keeps this a
genuinely separate process all the way to the edge (M10 R2): a bug in
this module can't affect Metro's app, and vice versa.

Generic pieces (rate limiting, connection caps, CORS, the SSE diff loop,
train/state shaping) are imported directly from `api.app`/`api.limits`
rather than duplicated — Phase A confirmed V/Line's VP/TU shape matches
Metro's exactly, so this reuse is real, not a guess.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from ..api.app import _client_ip, _cors_origins, _is_current, _train
from ..api.http_metrics import HttpMetricsMiddleware
from ..api.limits import (
    ConnectionLimitExceeded,
    ConnectionTracker,
    RateLimitExceeded,
    RateLimiter,
)
from ..api.schemas import DeltaResponse, FeedStatus, HealthResponse, StateResponse, Train
from ..gateway.client import Feed
from ..gtfs.schedule_cache import NoPinnedSnapshotError, PinnedScheduleCache
from ..metrics import STALENESS_THRESHOLD_S, Metrics
from ..poller.loop import PollerLoop
from ..state.eventhub import EventHub
from ..state.store import StateStore

logger = logging.getLogger("traintracker.vline_poller.app")

SSE_HEARTBEAT_INTERVAL_S = 20.0

# V/Line has no Service Alerts (M10 R5) — only these two feeds are ever
# fetched, so this is the only pair whose staleness this app reports.
VLINE_FEEDS: tuple[Feed, ...] = (Feed.TRIP_UPDATES, Feed.VEHICLE_POSITIONS)


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

    # Known-gap: V/Line ships without Service Alerts (M10 R5) — there is
    # deliberately no /api/vline/alerts route. The frontend (Phase C) must
    # state this plainly, not silently omit an alerts panel.

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

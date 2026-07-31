"""M3's FastAPI app: read-only, GET-only, serves 2d's derived state directly
out of the same process's memory -- no new path to the upstream API
(security invariant #1), this only ever reads what the poll loop already
fetched.

Docs/OpenAPI endpoints are disabled by default: this is a small, fixed,
already-documented (see ops/runbook.md and this milestone's spec) public
surface, not a browsable API product -- no reason to publish a live schema
explorer alongside it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from ..gateway.client import Feed
from ..gtfs.schedule import ScheduledDeparture
from ..gtfs.schedule_cache import NoPinnedSnapshotError, PinnedScheduleCache
from ..metrics import STALENESS_THRESHOLD_S
from ..poller.loop import ALL_FEEDS, PollerLoop
from ..state.eventhub import EventHub
from ..state.ghost import MAX_GHOST_AGE_S, TrackedTrainView
from ..state.store import StateStore
from .limits import (
    RATE_LIMIT_WINDOW_S,
    ConnectionLimitExceeded,
    ConnectionTracker,
    RateLimitExceeded,
    RateLimiter,
)
from .schemas import (
    AttributionResponse,
    DeltaResponse,
    FeedStatus,
    HealthResponse,
    ScheduledTrain,
    StateResponse,
    StationScheduleResponse,
    Train,
)

# M3 finding #11: both the static GTFS schedule and the realtime feeds are
# published by Vic DoT under CC BY 4.0 (confirmed live, M1 spike). Neither
# dataset page mandates an exact credit-line format, so this follows
# standard CC BY 4.0 practice: name the source, link the license text,
# note the data is derived/processed rather than the original feed as-is.
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

# M3 finding #5's resolution: this is a cap on connection *idleness*, not a
# promised data-freshness cadence -- the actual delta cadence is whatever
# the poll loop's real cadence is (10s or the overnight 30-60s), since a
# tick only fires after a real poll cycle completes. This value only
# controls how long a truly quiet connection goes before a keepalive
# comment, chosen to stay comfortably under the reverse-ingress layer's
# idle-connection timeout (check the deployed value before relying on this
# if that ever changes -- exposure/ingress specifics live in ops docs, not
# this repo).
SSE_HEARTBEAT_INTERVAL_S = 20.0


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
    # `last_seen_at` alone stayed None forever for those, the exact hole
    # that let stale ghosts accumulate and still pass this check (found
    # 2026-07-31, see state/ghost.py). `_trains` itself now also evicts on
    # this same age via `TrainLifecycleTracker._evict_stale` (called every
    # tick) -- this check exists in addition because a request can land
    # between ticks, when the tracker hasn't had a chance to sweep yet.
    if tracked.last_touched_at is None:
        return True  # not yet ticked even once; shouldn't happen in practice
    return (now - tracked.last_touched_at).total_seconds() <= MAX_GHOST_AGE_S


def _train(store: StateStore, tracked: TrackedTrainView) -> Train:
    snapshot = store.latest_snapshots.get(tracked.trip_id)
    if snapshot is not None:
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
        )

    # Dropped out of both live feeds entirely (coasting/ghost with only a
    # last-known fix) -- report what's actually known (position, honestly
    # timestamped via last_seen_at) rather than either inventing fresher
    # data or silently omitting the train from the response.
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
    )


def _scheduled_train(store: StateStore, dep: ScheduledDeparture) -> ScheduledTrain:
    """Overlays a live Trip Updates prediction onto one scheduled departure,
    when this process's own StateStore happens to have one for the exact
    (trip_id, platform) right now -- reads only already-polled in-memory
    state, no upstream call (invariant #1), same as every other route."""
    predicted_time: datetime | None = None
    delay_seconds: int | None = None
    is_cancelled = False
    snapshot = store.latest_snapshots.get(dep.trip_id)
    if snapshot is not None:
        is_cancelled = snapshot.schedule_relationship == "CANCELED"
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
    )


def _current_state(loop: PollerLoop, store: StateStore) -> StateResponse:
    now = datetime.now(timezone.utc)
    return StateResponse(
        generated_at=now,
        backoff_active=loop.breaker.backoff_active,
        feeds={feed.value: _feed_status(loop, feed, now) for feed in ALL_FEEDS},
        trains=[
            _train(store, tracked)
            for tracked in store.all_tracked()
            if _is_current(tracked, now)
        ],
    )


def _client_ip(request: Request) -> str:
    # `X-Forwarded-For` is normally not something to trust from a client --
    # here it's safe: per the M3 process-boundary decision, this container
    # only carries the new `ingress` network, shared solely with `caddy`.
    # Nothing else can reach this port to forge the header in the first
    # place, unlike a general public-internet-facing service.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
) -> AsyncIterator[str]:
    """The actual SSE event logic, independent of FastAPI/Starlette/ASGI --
    `is_disconnected` is injected rather than taking a `Request` so this can
    be driven directly in tests. (Found the hard way: httpx's `ASGITransport`
    fully awaits an ASGI app to completion before returning anything at all,
    so it cannot drive an infinite generator like this one -- there's no way
    to test the real route end-to-end without a live server. Extracting this
    function is what makes the actual event logic testable at all.)

    No shared ring buffer (M3's steelman-informed scope cut) -- `sent` is
    local to this one connection, diffed fresh against `store`/`loop` on
    every tick.
    """
    # Bounded to 1: this queue only ever carries "something changed, go
    # recompute" wake-ups (the value itself is never read below), so a
    # backlog beyond the single most recent tick is pure noise, never data
    # loss -- see state/eventhub.py's InProcessEventHub.
    queue = hub.subscribe(maxsize=1)
    try:
        state = _current_state(loop, store)
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

            state = _current_state(loop, store)
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

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Finding #8 (spec review): never let a stack trace or internal path
        # reach a public response, regardless of what FastAPI's own default
        # would otherwise do. Full detail stays server-side in the log.
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
        return _current_state(loop, store)

    @app.get(
        "/attribution",
        response_model=AttributionResponse,
        dependencies=[Depends(_rate_limit_dependency(rate_limiter, "attribution"))],
    )
    async def attribution() -> AttributionResponse:
        return DATA_ATTRIBUTION

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
            departures = schedule_cache.next_departures_for(station_id, now)
        except NoPinnedSnapshotError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if departures is None:
            raise HTTPException(status_code=404, detail=f"unknown station_id: {station_id}")
        return StationScheduleResponse(
            station_id=station_id,
            generated_at=now,
            departures=[_scheduled_train(store, dep) for dep in departures],
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
                    loop, store, hub, request.is_disconnected, heartbeat_interval_s
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

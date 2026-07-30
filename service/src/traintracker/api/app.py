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

import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..gateway.client import Feed
from ..metrics import STALENESS_THRESHOLD_S
from ..poller.loop import ALL_FEEDS, PollerLoop
from ..state.ghost import TrackedTrainView
from ..state.store import StateStore
from .schemas import FeedStatus, HealthResponse, StateResponse, Train

logger = logging.getLogger("traintracker.api")

CORS_ORIGINS_ENV = "TT_CORS_ORIGINS"

# A ghost/coasting train with no live-feed presence for longer than this is
# treated as journey-ended for API purposes. `state/ghost.py`'s tracker has
# no eviction of its own (CLAUDE.md's "ghost ... fade at journey end" was
# never actually built -- a separate, still-open gap, see M3 milestone doc)
# so `store.all_tracked()` can return arbitrarily old entries; this is a
# presentation-layer approximation to stop a live consumer being flooded
# with every trip ever seen since the process started, not a tuned value --
# generous relative to the ~90s-few-minute typical ghost durations seen in
# the 2g soak week.
MAX_GHOST_AGE_S = 2 * 60 * 60


def _cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV, "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _feed_status(loop: PollerLoop, feed: Feed, now: datetime) -> FeedStatus:
    changed_at = loop.last_changed_at(feed)
    stale = changed_at is None or (now - changed_at).total_seconds() > STALENESS_THRESHOLD_S
    return FeedStatus(last_changed_at=changed_at, stale=stale)


def _is_current(tracked: TrackedTrainView, now: datetime) -> bool:
    if tracked.last_seen_at is None:
        # Never seen a live position at all this session -- this is a
        # brand-new trip picked up from one feed only (e.g. scheduled in
        # TU, no VP fix yet), not a stale leftover. Nothing to filter on.
        return True
    return (now - tracked.last_seen_at).total_seconds() <= MAX_GHOST_AGE_S


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


def create_app(loop: PollerLoop, store: StateStore) -> FastAPI:
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

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/api/state", response_model=StateResponse)
    async def get_state() -> StateResponse:
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

    return app

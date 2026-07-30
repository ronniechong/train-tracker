"""M3 response-shape contract (finding #10 from the spec review) -- defined
once, explicitly, before the routes that use it, rather than letting FastAPI
infer a shape implicitly from whatever the state store happens to hold.

Every timestamp is UTC (project-wide convention). Feed staleness is included
per-feed in every response -- the poller's "0 entities but header still
advancing" honesty (CLAUDE.md's settled staleness decision) has to survive
into the public API, not just live on the internal dashboard.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FeedStatus(BaseModel):
    last_changed_at: datetime | None
    stale: bool


class Train(BaseModel):
    trip_id: str
    route_id: str | None
    status: str  # "live" | "coasting" | "ghost"
    latitude: float | None
    longitude: float | None
    bearing: float | None
    position_updated_at: datetime | None
    schedule_updated_at: datetime | None
    # Distinct from position_updated_at: set for every train regardless of
    # whether it's still present in the live feeds, so a fully-vanished
    # ghost (route_id/position_updated_at all null -- see api/app.py) still
    # carries an honest "last confirmed at" timestamp instead of looking
    # like a fresh, currently-unlocated train.
    last_seen_at: datetime | None


class StateResponse(BaseModel):
    generated_at: datetime
    backoff_active: bool
    feeds: dict[str, FeedStatus]
    trains: list[Train]


class HealthResponse(BaseModel):
    status: str

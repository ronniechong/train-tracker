"""The 3 read-only agent tools scoped at M5 kickoff: get_line_status,
get_trip, get_active_alerts. All three read ONLY this process's own
already-polled local state (`StateStore.latest_snapshots`/`.latest_alerts`,
`PinnedScheduleCache`'s already-pinned static snapshot) -- security
invariant #1 (exactly one upstream consumer, the poller) means no tool
here may ever trigger a fresh request to the upstream GTFS-R API, no
matter what a user or the LLM asks for.

Each tool is a plain async function taking a shared `ToolContext` plus its
own keyword arguments (the LLM's `tool_use.input`, unpacked by
`ai/agent.py`), returning a plain JSON-serialisable dict -- errors are
returned as `{"error": ...}` payloads the model can read and react to,
not raised exceptions, for the expected cases (unknown line, untracked
trip). `TOOLS` is the Anthropic tool-schema list handed to
`LLMClient.complete()`; `TOOL_FUNCTIONS` is the name -> callable registry
`ai/agent.py`'s loop dispatches through.

`get_trip`/`get_line_status` also carry ghost-inference evidence
(`position_source`, `last_seen_at`, `ghost_duration_s` -- 2026-08-02,
`state/ghost.py`'s `TrackedTrainView`): a caller must not treat a
`position_source` of `"last_confirmed"` as equivalent to `"live"` when
narrating a position, per CLAUDE.md invariant 7 (every inference
labelled with its evidence). The narration-level instruction lives in
each caller's system prompt (e.g. `ai/briefing.py`), not here -- this
module only supplies the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..gtfs.routes import find_routes_by_name, replacement_bus_route_id
from ..gtfs.schedule_cache import PinnedScheduleCache
from ..state.alerts import Alert, alerts_matching
from ..state.store import StateStore


@dataclass(frozen=True)
class ToolContext:
    store: StateStore
    schedule_cache: PinnedScheduleCache


def _alert_summary(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "cause": alert.cause,
        "effect": alert.effect,
        "header_text": alert.header_text,
    }


def _line_route_ids(ctx: ToolContext, now: datetime, line_name: str) -> list[str] | None:
    """Resolves a line name to its route_id(s) -- the real line's own id
    PLUS its bus-replacement pair (see gtfs/routes.py), since a real
    disruption's alerts/trips can reference either. Returns `None` if the
    name matches nothing."""
    routes = find_routes_by_name(ctx.schedule_cache.routes_for(now), line_name)
    if not routes:
        return None
    route_ids: set[str] = set()
    for route in routes:
        route_ids.add(route.route_id)
        route_ids.add(replacement_bus_route_id(route.route_id))
    return sorted(route_ids)


def _ghost_evidence(tracked, now: datetime) -> dict[str, Any]:
    """Shared evidence fields for a tracked (possibly ghost/coasting) trip
    -- 2026-08-02, ghost-inference annotations. `tracked` is a
    `TrackedTrainView` or `None` (never ticked by the lifecycle tracker at
    all, e.g. a snapshot injected directly in a test without `ingest()`).

    `position_source` distinguishes a real live fix ("live") from a
    last-known live fix carried forward while ghost/coasting
    ("last_confirmed") -- deliberately NOT "scheduled": a genuine
    schedule-derived position for a fully-vanished ghost was never built
    (see `state/ghost.py`'s own docstring, M4-scoped), so this must not
    imply a fidelity the system doesn't actually have."""
    if tracked is None:
        return {"last_seen_at": None, "ghost_duration_s": None}
    ghost_duration_s = (
        (now - tracked.ghost_started_at).total_seconds()
        if tracked.status == "ghost" and tracked.ghost_started_at is not None
        else None
    )
    return {
        "last_seen_at": tracked.last_seen_at.isoformat() if tracked.last_seen_at else None,
        "ghost_duration_s": ghost_duration_s,
    }


async def get_trip(ctx: ToolContext, *, trip_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    snapshot = ctx.store.latest_snapshots.get(trip_id)
    tracked = ctx.store.view_of(trip_id)
    if snapshot is None and tracked is None:
        return {"error": f"trip_id {trip_id!r} is not currently tracked"}

    if snapshot is not None and snapshot.has_position:
        position_source = "live"
        latitude, longitude = snapshot.latitude, snapshot.longitude
    elif tracked is not None and tracked.last_position is not None:
        position_source = "last_confirmed"
        latitude, longitude = tracked.last_position
    else:
        position_source = None
        latitude, longitude = None, None

    result: dict[str, Any] = {
        "trip_id": trip_id,
        "status": tracked.status if tracked is not None else ctx.store.status_of(trip_id),
        "position_source": position_source,
        "latitude": latitude,
        "longitude": longitude,
        "route_id": snapshot.route_id if snapshot is not None else None,
        "is_cancelled": snapshot.schedule_relationship == "CANCELED" if snapshot is not None else None,
        "is_added": snapshot.schedule_relationship == "ADDED" if snapshot is not None else None,
        "stops": [
            {
                "stop_id": stu.stop_id,
                "arrival_delay_s": stu.arrival_delay,
                "departure_delay_s": stu.departure_delay,
                "schedule_relationship": stu.schedule_relationship,
            }
            for stu in snapshot.stop_time_updates
        ] if snapshot is not None else [],
    }
    result.update(_ghost_evidence(tracked, now))
    return result


async def get_active_alerts(ctx: ToolContext, *, line_name: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if line_name is None:
        matched = alerts_matching(ctx.store.latest_alerts, now)
    else:
        route_ids = _line_route_ids(ctx, now, line_name)
        if route_ids is None:
            return {"error": f"no line found matching {line_name!r}"}
        seen: dict[str, Alert] = {}
        for route_id in route_ids:
            for alert in alerts_matching(ctx.store.latest_alerts, now, route_id=route_id):
                seen[alert.id] = alert
        matched = list(seen.values())
    return {"alerts": [_alert_summary(a) for a in matched]}


async def get_line_status(ctx: ToolContext, *, line_name: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    route_ids = _line_route_ids(ctx, now, line_name)
    if route_ids is None:
        return {"error": f"no line found matching {line_name!r}"}

    status_counts = {"live": 0, "coasting": 0, "ghost": 0}
    cancelled_trip_ids: list[str] = []
    # Per-trip evidence for non-live trips only -- 2026-08-02, ghost-
    # inference annotations -- so a briefing/query can cite "trip X has
    # been ghost-tracked for Ys" rather than just a bare count. Same
    # route-attribution limit as before: a trip that has dropped out of
    # BOTH feeds entirely loses its route_id project-wide (see
    # `api/app.py`'s `_train`, which accepts the identical gap), so this
    # can only cover coasting/ghost trips still present in this cycle's
    # merge output, not fully-vanished ones -- unlike `get_trip`, which
    # has no such limit once a trip_id is already known.
    non_live_evidence: list[dict[str, Any]] = []
    route_id_set = set(route_ids)
    for snapshot in ctx.store.latest_snapshots.values():
        if snapshot.route_id not in route_id_set:
            continue
        tracked = ctx.store.view_of(snapshot.trip_id)
        status = tracked.status if tracked is not None else None
        if status in status_counts:
            status_counts[status] += 1
        if status in ("coasting", "ghost"):
            non_live_evidence.append({"trip_id": snapshot.trip_id, "status": status, **_ghost_evidence(tracked, now)})
        if snapshot.schedule_relationship == "CANCELED":
            cancelled_trip_ids.append(snapshot.trip_id)

    alerts = await get_active_alerts(ctx, line_name=line_name)

    return {
        "line_name": line_name,
        "route_ids": route_ids,
        "tracked_trip_counts": status_counts,
        "non_live_trips": non_live_evidence,
        "cancelled_trip_ids": cancelled_trip_ids,
        "active_alerts": alerts["alerts"],
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_line_status",
        "description": (
            "Get the current status of one Melbourne metro train line by name "
            "(e.g. 'Belgrave', 'Frankston', 'City Circle'): how many trains are "
            "currently live/coasting/ghost-tracked, evidence (last_seen_at, "
            "ghost_duration_s) for the non-live ones, any cancelled trip_ids, "
            "and any active Service Alerts for that line. Use this when asked "
            "how a specific line is running or whether it's disrupted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_name": {
                    "type": "string",
                    "description": "Line name as a rider would say it, e.g. 'Belgrave'.",
                },
            },
            "required": ["line_name"],
        },
    },
    {
        "name": "get_trip",
        "description": (
            "Get full detail for one specific trip_id: route, cancellation/"
            "extra-service status, current position, per-stop delay/skip info, "
            "and position_source ('live' vs 'last_confirmed' -- a non-live "
            "position is stale, not a current fact, treat and narrate it "
            "accordingly). Still returns ghost evidence even for a trip that "
            "has dropped out of both live feeds entirely. trip_id usually "
            "comes from another tool's output, not from a bare user question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"trip_id": {"type": "string"}},
            "required": ["trip_id"],
        },
    },
    {
        "name": "get_active_alerts",
        "description": (
            "List currently active Service Alerts, network-wide or filtered to "
            "one line by name. Each alert is a coarse route/stop match from the "
            "upstream feed, never confirmation that a specific train is affected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_name": {
                    "type": "string",
                    "description": "Optional line name to filter to, e.g. 'Belgrave'. Omit for all active alerts.",
                },
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_line_status": get_line_status,
    "get_trip": get_trip,
    "get_active_alerts": get_active_alerts,
}

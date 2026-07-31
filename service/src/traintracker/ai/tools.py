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


async def get_trip(ctx: ToolContext, *, trip_id: str) -> dict[str, Any]:
    snapshot = ctx.store.latest_snapshots.get(trip_id)
    if snapshot is None:
        return {"error": f"trip_id {trip_id!r} is not currently tracked"}
    return {
        "trip_id": snapshot.trip_id,
        "route_id": snapshot.route_id,
        "status": ctx.store.status_of(trip_id),
        "is_cancelled": snapshot.schedule_relationship == "CANCELED",
        "is_added": snapshot.schedule_relationship == "ADDED",
        "latitude": snapshot.latitude,
        "longitude": snapshot.longitude,
        "stops": [
            {
                "stop_id": stu.stop_id,
                "arrival_delay_s": stu.arrival_delay,
                "departure_delay_s": stu.departure_delay,
                "schedule_relationship": stu.schedule_relationship,
            }
            for stu in snapshot.stop_time_updates
        ],
    }


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
    route_id_set = set(route_ids)
    for snapshot in ctx.store.latest_snapshots.values():
        if snapshot.route_id not in route_id_set:
            continue
        status = ctx.store.status_of(snapshot.trip_id)
        if status in status_counts:
            status_counts[status] += 1
        if snapshot.schedule_relationship == "CANCELED":
            cancelled_trip_ids.append(snapshot.trip_id)

    alerts = await get_active_alerts(ctx, line_name=line_name)

    return {
        "line_name": line_name,
        "route_ids": route_ids,
        "tracked_trip_counts": status_counts,
        "cancelled_trip_ids": cancelled_trip_ids,
        "active_alerts": alerts["alerts"],
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_line_status",
        "description": (
            "Get the current status of one Melbourne metro train line by name "
            "(e.g. 'Belgrave', 'Frankston', 'City Circle'): how many trains are "
            "currently live/coasting/ghost-tracked, any cancelled trip_ids, and "
            "any active Service Alerts for that line. Use this when asked how a "
            "specific line is running or whether it's disrupted."
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
            "Get full live detail for one specific trip_id: route, cancellation/"
            "extra-service status, current position, and per-stop delay/skip "
            "info. trip_id usually comes from another tool's output, not from a "
            "bare user question."
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

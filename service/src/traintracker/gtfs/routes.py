"""Static GTFS `routes.txt` — route_id -> friendly line name.

Needed only by the AI layer's name-facing tools (`ai/tools.py`): every
other module in this codebase works with `route_id` directly, since
that's what the realtime feeds already key on. An LLM (or a human typing
a question) speaks in line names ("Belgrave"), not GTFS URNs, so this is
the one place that bridges the two.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    route_id: str
    short_name: str
    long_name: str


def parse_routes(routes_txt: str) -> dict[str, Route]:
    routes = {}
    for row in csv.DictReader(io.StringIO(routes_txt)):
        route_id = row["route_id"]
        routes[route_id] = Route(
            route_id=route_id,
            short_name=row.get("route_short_name", ""),
            long_name=row.get("route_long_name", ""),
        )
    return routes


def routes_from_zip_bytes(data: bytes) -> dict[str, Route]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        routes_txt = zf.read("routes.txt").decode("utf-8-sig")
    return parse_routes(routes_txt)


# Every real line is paired with a second "-R" route_id whose
# route_short_name is always this literal string (route_long_name and
# route_id are what actually distinguish which line it belongs to) --
# this is a bus-replacement variant of the parent line, not a reverse
# direction (direction is `trip.direction_id`, a wholly separate concept).
# Never a valid match target for `find_routes_by_name`.
REPLACEMENT_BUS_SHORT_NAME = "Replacement Bus"


def replacement_bus_route_id(route_id: str) -> str:
    """The bus-replacement route_id paired with a real line's route_id.
    GTFS-R alerts reference both ids during a real disruption -- callers
    that filter by line name need to check both, not just the line's own
    base id."""
    return route_id.rstrip(":") + "-R:"


def find_routes_by_name(routes: dict[str, Route], name: str) -> list[Route]:
    """Case-insensitive match against `short_name`: exact match first,
    falling back to substring so "glen wav" still finds "Glen Waverley".
    Never matches a Replacement Bus row by name -- those are only reached
    via `replacement_bus_route_id` alongside their real line."""
    needle = name.strip().lower()
    if not needle:
        return []
    candidates = [r for r in routes.values() if r.short_name != REPLACEMENT_BUS_SHORT_NAME]
    exact = [r for r in candidates if r.short_name.lower() == needle]
    if exact:
        return exact
    return [r for r in candidates if needle in r.short_name.lower()]

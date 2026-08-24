"""Station NAME -> station_id resolution, for M13's public API. Distinct
from `schedule.py`'s `platforms_for_station`, which goes the other
direction (a known station_id -> its platform stop_ids) -- this module is
the new direction needed once an endpoint accepts free-text names instead
of ids, per M13's resolved Finding 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from .stops import Stop


@dataclass(frozen=True)
class StationMatch:
    station_id: str
    name: str


def canonical_stations(stops: dict[str, Stop]) -> dict[str, str]:
    """station_id -> display name, one entry per STATION (not per
    platform). A station_id here is whatever `platforms_for_station`
    would accept: either a true parent station (referenced as some other
    stop's `parent_station`) or a standalone stop with no parent split in
    the data (`platforms_for_station`'s own fallback case) -- never a
    platform stop_id that belongs to a parent."""
    parent_ids = {s.parent_station for s in stops.values() if s.parent_station}
    return {
        sid: stop.name
        for sid, stop in stops.items()
        if sid in parent_ids or stop.parent_station is None
    }


def find_stations_by_name(stops: dict[str, Stop], name: str) -> list[StationMatch]:
    """Case-insensitive match against station display names: exact match
    first, falling back to substring -- same two-tier convention as
    `routes.find_routes_by_name`, so "flinders" alone still resolves
    unambiguously to Flinders Street Station via the exact-tier check."""
    needle = name.strip().lower()
    if not needle:
        return []
    stations = canonical_stations(stops)
    exact = [
        StationMatch(station_id=sid, name=n) for sid, n in stations.items() if n.lower() == needle
    ]
    if exact:
        return exact
    return [
        StationMatch(station_id=sid, name=n)
        for sid, n in stations.items()
        if needle in n.lower()
    ]


def narrow_by_route(
    matches: list[StationMatch], route_ids: set[str], stop_routes: dict[str, frozenset[str]]
) -> list[StationMatch]:
    """When a name match is ambiguous, narrow to only the candidates whose
    station_id is actually served by one of `route_ids` (the resolved
    route(s) matching the caller's optional `route` disambiguator).
    Returns the ORIGINAL list unchanged if narrowing would eliminate every
    candidate -- an unrecognized/wrong `route` value should not silently
    turn an ambiguous-but-resolvable name into a false `unknown_station`,
    it should fall through to the caller's own ambiguity handling."""
    narrowed = [m for m in matches if stop_routes.get(m.station_id, frozenset()) & route_ids]
    return narrowed or matches

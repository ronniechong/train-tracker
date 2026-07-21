"""TU/VP merge: combine one Trip Updates poll and one Vehicle Positions poll
into a single per-trip snapshot, TU-primary / VP-secondary per CLAUDE.md's
settled feed-roles decision.

Per-field freshness, not a single blanket "last updated": schedule fields
(route_id, stop_time_update, ...) carry TU's timestamp, position fields carry
VP's, because the two feeds are read at different cadences (TU ~10s, VP
~29-30s per M1) and a caller reasoning about staleness needs to know which
half of a snapshot is old, not just that *something* is old.

This module is a pure, single-cycle merge — it has no memory of previous
polls. Carrying a snapshot forward across polls where a trip drops out of
one or both feeds (coasting/ghost) is the state store's job, layered on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _epoch_to_utc(epoch_str: str | None) -> datetime | None:
    if epoch_str is None:
        return None
    return datetime.fromtimestamp(int(epoch_str), tz=timezone.utc)


@dataclass(frozen=True)
class StopTimeUpdate:
    stop_sequence: int
    stop_id: str | None
    arrival_delay: int | None
    arrival_time: int | None
    departure_delay: int | None
    departure_time: int | None
    schedule_relationship: str | None


@dataclass(frozen=True)
class TrainSnapshot:
    trip_id: str

    # Schedule group - TU-primary.
    route_id: str | None
    start_time: str | None
    start_date: str | None
    schedule_relationship: str | None
    stop_time_updates: tuple[StopTimeUpdate, ...]
    schedule_updated_at: datetime | None  # None => trip absent from this TU poll

    # Position group - VP-secondary.
    latitude: float | None
    longitude: float | None
    bearing: float | None
    position_updated_at: datetime | None  # None => trip absent from this VP poll

    @property
    def has_schedule(self) -> bool:
        return self.schedule_updated_at is not None

    @property
    def has_position(self) -> bool:
        return self.position_updated_at is not None


@dataclass(frozen=True)
class DiscrepancyEvent:
    """A conflict between TU and VP for a trip present in both feeds this
    cycle. Schema designed now (2d) so 2f's metrics and 2g's discrepancy-rate
    gate have a stable shape to read from later - see finding #5.

    Deliberately excludes trips present in only one feed: TU-without-VP is
    the normal coasting/ghost baseline (the state machine's job, not a data
    conflict), and is not logged here."""

    trip_id: str
    observed_at: datetime
    discrepancy_type: str  # "route_id_mismatch" | "start_time_mismatch" |
    #                        "start_date_mismatch" | "schedule_relationship_mismatch" |
    #                        "vp_without_tu"
    tu_value: str | None
    vp_value: str | None


def _stop_time_updates(raw: list[dict]) -> tuple[StopTimeUpdate, ...]:
    result = []
    for stu in raw:
        arrival = stu.get("arrival") or {}
        departure = stu.get("departure") or {}
        result.append(
            StopTimeUpdate(
                stop_sequence=stu["stop_sequence"],
                stop_id=stu.get("stop_id"),
                arrival_delay=arrival.get("delay"),
                arrival_time=arrival.get("time"),
                departure_delay=departure.get("delay"),
                departure_time=departure.get("time"),
                schedule_relationship=stu.get("schedule_relationship"),
            )
        )
    return tuple(result)


def _tu_index(tu_feed: dict) -> dict[str, dict]:
    """trip_id -> trip_update dict, for every entity in this TU poll."""
    index = {}
    for entity in tu_feed.get("entity", []):
        tu = entity.get("trip_update")
        if not tu:
            continue
        trip_id = tu.get("trip", {}).get("trip_id")
        if trip_id is not None:
            index[trip_id] = tu
    return index


def _vp_index(vp_feed: dict) -> dict[str, dict]:
    """trip_id -> vehicle dict, for every entity in this VP poll."""
    index = {}
    for entity in vp_feed.get("entity", []):
        vehicle = entity.get("vehicle")
        if not vehicle:
            continue
        trip_id = vehicle.get("trip", {}).get("trip_id")
        if trip_id is not None:
            index[trip_id] = vehicle
    return index


def merge(tu_feed: dict, vp_feed: dict) -> tuple[dict[str, TrainSnapshot], list[DiscrepancyEvent]]:
    """Merge one decoded TU FeedMessage + one decoded VP FeedMessage (the
    `feed` sub-object of a capture record, i.e. {"header": ..., "entity": [...]})
    into per-trip snapshots, keyed by trip_id, plus any TU/VP discrepancies
    found for trips present in both.

    Every trip_id appearing in EITHER feed gets a snapshot - a trip known
    only to TU (no live position yet, or ghosted) is a normal, expected
    state, not an error.
    """
    tu_by_trip = _tu_index(tu_feed)
    vp_by_trip = _vp_index(vp_feed)
    tu_header_ts = _epoch_to_utc(tu_feed.get("header", {}).get("timestamp"))

    snapshots: dict[str, TrainSnapshot] = {}
    discrepancies: list[DiscrepancyEvent] = []

    for trip_id in tu_by_trip.keys() | vp_by_trip.keys():
        tu = tu_by_trip.get(trip_id)
        vp = vp_by_trip.get(trip_id)

        if tu is not None:
            tu_trip = tu.get("trip", {})
            route_id = tu_trip.get("route_id")
            start_time = tu_trip.get("start_time")
            start_date = tu_trip.get("start_date")
            schedule_relationship = tu_trip.get("schedule_relationship")
            stop_time_updates = _stop_time_updates(tu.get("stop_time_update", []))
            schedule_updated_at = tu_header_ts
        else:
            route_id = start_time = start_date = schedule_relationship = None
            stop_time_updates = ()
            schedule_updated_at = None

        if vp is not None:
            vp_trip = vp.get("trip", {})
            position = vp.get("position", {})
            latitude = position.get("latitude")
            longitude = position.get("longitude")
            bearing = position.get("bearing")
            position_updated_at = _epoch_to_utc(vp.get("timestamp"))
            # Fall back to VP entities that have no per-entity timestamp
            # (not observed in practice, but the field is optional in the spec).
            if position_updated_at is None:
                position_updated_at = _epoch_to_utc(vp_feed.get("header", {}).get("timestamp"))
        else:
            vp_trip = {}
            latitude = longitude = bearing = None
            position_updated_at = None

        snapshots[trip_id] = TrainSnapshot(
            trip_id=trip_id,
            route_id=route_id,
            start_time=start_time,
            start_date=start_date,
            schedule_relationship=schedule_relationship,
            stop_time_updates=stop_time_updates,
            schedule_updated_at=schedule_updated_at,
            latitude=latitude,
            longitude=longitude,
            bearing=bearing,
            position_updated_at=position_updated_at,
        )

        if tu is None and vp is not None:
            discrepancies.append(
                DiscrepancyEvent(
                    trip_id=trip_id,
                    observed_at=position_updated_at or datetime.now(timezone.utc),
                    discrepancy_type="vp_without_tu",
                    tu_value=None,
                    vp_value=vp_trip.get("route_id"),
                )
            )
        elif tu is not None and vp is not None:
            observed_at = tu_header_ts or datetime.now(timezone.utc)
            vp_route_id = vp_trip.get("route_id")
            vp_start_time = vp_trip.get("start_time")
            vp_start_date = vp_trip.get("start_date")
            vp_schedule_relationship = vp_trip.get("schedule_relationship")

            if vp_route_id is not None and vp_route_id != route_id:
                discrepancies.append(DiscrepancyEvent(
                    trip_id, observed_at, "route_id_mismatch", route_id, vp_route_id
                ))
            if vp_start_time is not None and vp_start_time != start_time:
                discrepancies.append(DiscrepancyEvent(
                    trip_id, observed_at, "start_time_mismatch", start_time, vp_start_time
                ))
            if vp_start_date is not None and vp_start_date != start_date:
                discrepancies.append(DiscrepancyEvent(
                    trip_id, observed_at, "start_date_mismatch", start_date, vp_start_date
                ))
            if vp_schedule_relationship is not None and vp_schedule_relationship != schedule_relationship:
                discrepancies.append(DiscrepancyEvent(
                    trip_id, observed_at, "schedule_relationship_mismatch",
                    schedule_relationship, vp_schedule_relationship,
                ))

    return snapshots, discrepancies

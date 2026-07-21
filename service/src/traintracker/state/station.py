"""Station-state derivation: where is this train right now, "at" a stop or
"between" two - per CLAUDE.md's settled decision, since Vehicle Positions
never populates `stop_id`/`current_status` (M1: 0% for both).

TU is the primary signal here, not geofence-first: `stop_time_update`
entries carry the real schedule (predicted arrival/departure per stop, in
order), so walking that list against "now" tells us which stop is current
without needing GPS at all - the same reasoning the ghost state machine will
reuse to render a scheduled position when there's no live fix at all.

Geofence is the *cross-check*, not the primary source: when a live position
is available, we confirm it against the schedule-implied stop(s)' real-world
coordinates from stops.txt. Confirmation rides alongside the schedule-derived
status rather than overriding it - VP's own position can itself be a stale,
carried-forward fix (the coasting case), so a geofence miss is a signal to
surface, not a reason to discard TU's read of where the train actually is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .geo import haversine_m
from .merge import StopTimeUpdate, TrainSnapshot
from ..gtfs.stops import Stop

# First-cut estimate, not validated against real platform-dwell GPS traces -
# revisit if the geofence cross-check disagrees often enough to be noisy
# rather than informative (mirrors CITY_LOOP_BBOX's "loosely drawn" caveat).
GEOFENCE_RADIUS_M = 100.0

Status = Literal["at", "between", "unknown"]


@dataclass(frozen=True)
class StationState:
    status: Status
    at_stop_id: str | None = None
    from_stop_id: str | None = None
    to_stop_id: str | None = None
    progress: float | None = None  # 0.0-1.0, only set when status == "between"
    # None: no live position to check. True/False: position was/wasn't within
    # GEOFENCE_RADIUS_M of the schedule-implied stop(s).
    geofence_confirmed: bool | None = None


def _epoch(value: str | int | None) -> int | None:
    return int(value) if value is not None else None


@dataclass(frozen=True)
class _Anchor:
    stop_id: str | None
    arrival: int
    departure: int
    # Whether the RAW feed entry actually carried this field, vs. it being
    # gap-filled from the other side. This is the only reliable signal for
    # "is this genuinely the trip's first/last stop" - `stop_time_update` is
    # a rolling window that trims stops off BOTH ends as a trip progresses
    # (confirmed against real captures: single-entry lists are common, ~8.7k
    # in one 3.5h slice), so "index 0 in today's list" does not mean "the
    # trip's actual origin".
    had_arrival: bool
    had_departure: bool


def _anchors(stus: tuple[StopTimeUpdate, ...]) -> list[_Anchor]:
    """One (arrival, departure) pair per stop, gap-filling whichever side is
    absent so every anchor has both bounds to compare `now` against. Stops
    with neither field are dropped - GTFS-RT requires at least one, but a
    malformed poll shouldn't crash derivation, just lose that one stop as a
    boundary candidate."""
    out = []
    for stu in stus:
        arrival = _epoch(stu.arrival_time)
        departure = _epoch(stu.departure_time)
        if arrival is None and departure is None:
            continue
        out.append(_Anchor(
            stop_id=stu.stop_id,
            arrival=arrival if arrival is not None else departure,
            departure=departure if departure is not None else arrival,
            had_arrival=arrival is not None,
            had_departure=departure is not None,
        ))
    return out


def _geofence_check(
    candidate_stop_ids: list[str | None],
    latitude: float | None,
    longitude: float | None,
    stops: dict[str, Stop],
) -> bool | None:
    if latitude is None or longitude is None:
        return None
    for stop_id in candidate_stop_ids:
        stop = stops.get(stop_id) if stop_id else None
        if stop is None:
            continue
        if haversine_m(latitude, longitude, stop.latitude, stop.longitude) <= GEOFENCE_RADIUS_M:
            return True
    return False


def derive_station_state(
    snapshot: TrainSnapshot, stops: dict[str, Stop], now: datetime,
) -> StationState:
    anchors = _anchors(snapshot.stop_time_updates)
    if not anchors:
        return StationState(status="unknown")

    now_epoch = int(now.timestamp())

    first = anchors[0]
    if now_epoch < first.arrival:
        confirmed = _geofence_check([first.stop_id], snapshot.latitude, snapshot.longitude, stops)
        if not first.had_arrival:
            # Genuine trip origin (no predecessor stop exists) - dwelling
            # before its first departure.
            return StationState(status="at", at_stop_id=first.stop_id, geofence_confirmed=confirmed)
        # This stop DID carry a real arrival prediction, so it has a
        # predecessor that the rolling window has already trimmed off the
        # front. We know we're heading toward `first`, not that we're
        # sitting at it - and we don't know when we left the trimmed
        # predecessor, so progress is honestly unknown rather than guessed.
        return StationState(
            status="between", from_stop_id=None, to_stop_id=first.stop_id,
            progress=None, geofence_confirmed=confirmed,
        )

    last = anchors[-1]
    if now_epoch >= last.departure:
        confirmed = _geofence_check([last.stop_id], snapshot.latitude, snapshot.longitude, stops)
        if not last.had_departure:
            # Genuine terminus (no successor stop exists).
            return StationState(status="at", at_stop_id=last.stop_id, geofence_confirmed=confirmed)
        # Departed `last` for real, but the rolling window hasn't yet
        # surfaced a prediction for whatever comes next.
        return StationState(
            status="between", from_stop_id=last.stop_id, to_stop_id=None,
            progress=None, geofence_confirmed=confirmed,
        )

    for cur, nxt in zip(anchors, anchors[1:]):
        if cur.arrival <= now_epoch <= cur.departure:
            confirmed = _geofence_check([cur.stop_id], snapshot.latitude, snapshot.longitude, stops)
            return StationState(status="at", at_stop_id=cur.stop_id, geofence_confirmed=confirmed)
        if cur.departure <= now_epoch < nxt.arrival:
            span = nxt.arrival - cur.departure
            progress = 0.0 if span <= 0 else (now_epoch - cur.departure) / span
            progress = min(1.0, max(0.0, progress))
            confirmed = _geofence_check(
                [cur.stop_id, nxt.stop_id], snapshot.latitude, snapshot.longitude, stops
            )
            return StationState(
                status="between", from_stop_id=cur.stop_id, to_stop_id=nxt.stop_id,
                progress=progress, geofence_confirmed=confirmed,
            )

    # Bounds-checked above (now_epoch is within [anchors[0].arrival,
    # anchors[-1].departure)), so every instant falls in some anchor's dwell
    # window or some cur/nxt gap - this is an unreachable safety net.
    return StationState(status="unknown")

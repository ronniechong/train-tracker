"""Live -> coasting -> ghost state machine (CLAUDE.md's settled "missing
trains" decision), stateful across poll cycles - the layer `merge.py`
explicitly defers ("carrying a snapshot forward across polls ... is the
state store's job, layered on top").

Coasting is not a separate trigger, just elapsed time since a trip last had
a live VP position: under `COASTING_TIMEOUT_S` -> "coasting" (keep showing
the last fix), at/over it -> "ghost" (render the scheduled position
instead, handled by station.py once schedule-only rendering lands).

Finding #6 (backoff must not ghost every train): a poller-wide backoff is
signalled per tick via `backoff_active`. While active, NO train's coasting
clock advances - the state machine literally cannot age a live train into
"ghost" during a backoff, rather than relying on a label to suppress it
after the fact. `GhostEvent.backoff_overlapped` records whether backoff
touched the gap at all, for 2f/2g's benefit, but is not what prevents the
false ghosting - the frozen clock is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from .eventlog import EventLog
from .geo import in_city_loop_bbox
from .merge import TrainSnapshot

# Midpoint of CLAUDE.md's settled "~60-90s" coasting window; matches the
# ghost-eligible threshold `spike/loop_gap_estimate.py` used for the 2d
# pre-analysis. First-cut constant, revisit at 2g like GEOFENCE_RADIUS_M.
COASTING_TIMEOUT_S = 90.0

Status = Literal["live", "coasting", "ghost"]


@dataclass(frozen=True)
class GhostEvent:
    trip_id: str
    last_seen_at: datetime | None  # None if never seen live at all this session
    last_seen_position: tuple[float, float] | None
    reappeared_at: datetime | None  # None if flushed while still ghosted
    reappear_position: tuple[float, float] | None
    loop_contained: bool  # both endpoints inside CITY_LOOP_BBOX; False if reappear_position unknown
    ghost_duration_s: float | None
    backoff_overlapped: bool  # whether any tick during the gap was backoff-skipped


@dataclass
class _TrackedTrain:
    status: Status = "live"
    last_seen_at: datetime | None = None
    last_position: tuple[float, float] | None = None
    coasting_elapsed: timedelta = field(default_factory=timedelta)
    backoff_overlapped: bool = False
    ghost_started_at: datetime | None = None


class TrainLifecycleTracker:
    def __init__(
        self,
        event_log: EventLog,
        coasting_timeout: timedelta = timedelta(seconds=COASTING_TIMEOUT_S),
    ):
        self._event_log = event_log
        self._coasting_timeout = coasting_timeout
        self._trains: dict[str, _TrackedTrain] = {}
        self._last_tick_at: datetime | None = None

    def tick(
        self,
        snapshots: dict[str, TrainSnapshot],
        cycle_time: datetime,
        backoff_active: bool = False,
    ) -> None:
        """Update every known trip plus any newly-seen trip_id this cycle.
        Emits a `GhostEvent` via the event log for each ghost that
        reappears this cycle."""
        delta = cycle_time - self._last_tick_at if self._last_tick_at is not None else timedelta()
        self._last_tick_at = cycle_time

        for trip_id in self._trains.keys() | snapshots.keys():
            tracked = self._trains.setdefault(trip_id, _TrackedTrain())
            snap = snapshots.get(trip_id)
            position = (snap.latitude, snap.longitude) if snap and snap.has_position else None

            if position is not None:
                if tracked.status == "ghost":
                    self._emit_reappearance(trip_id, tracked, cycle_time, position)
                tracked.status = "live"
                tracked.last_seen_at = cycle_time
                tracked.last_position = position
                tracked.coasting_elapsed = timedelta()
                tracked.backoff_overlapped = False
                tracked.ghost_started_at = None
                continue

            if tracked.last_seen_at is None:
                # Never seen a live position at all (picked up mid-trip
                # already missing VP, or hasn't started broadcasting yet) -
                # "coasting" implies a real last-known fix to keep showing,
                # which we don't have here. Go straight to "ghost" (render
                # the scheduled position) rather than invent one.
                tracked.status = "ghost"
                continue

            if backoff_active:
                tracked.backoff_overlapped = True
                # Clock frozen: elapsed does not advance, so this tick alone
                # can never push a train past the ghost threshold.
            else:
                tracked.coasting_elapsed += delta

            was_ghost = tracked.status == "ghost"
            if tracked.coasting_elapsed >= self._coasting_timeout:
                tracked.status = "ghost"
                if not was_ghost:
                    tracked.ghost_started_at = cycle_time
            else:
                tracked.status = "coasting"

    def status_of(self, trip_id: str) -> Status | None:
        tracked = self._trains.get(trip_id)
        return tracked.status if tracked else None

    def flush(self, at: datetime) -> None:
        """Force-close any still-open ghost episodes (e.g. at the end of a
        replay run) so they aren't silently dropped from the event log."""
        for trip_id, tracked in self._trains.items():
            if tracked.status == "ghost":
                self._emit_reappearance(trip_id, tracked, at, reappear_position=None)

    def _emit_reappearance(
        self,
        trip_id: str,
        tracked: _TrackedTrain,
        at: datetime,
        reappear_position: tuple[float, float] | None,
    ) -> None:
        loop_contained = (
            reappear_position is not None
            and tracked.last_position is not None
            and in_city_loop_bbox(*tracked.last_position)
            and in_city_loop_bbox(*reappear_position)
        )
        ghost_duration = (
            (at - tracked.ghost_started_at).total_seconds()
            if tracked.ghost_started_at is not None
            else None
        )
        self._event_log.record(GhostEvent(
            trip_id=trip_id,
            last_seen_at=tracked.last_seen_at,
            last_seen_position=tracked.last_position,
            reappeared_at=at if reappear_position is not None else None,
            reappear_position=reappear_position,
            loop_contained=loop_contained,
            ghost_duration_s=ghost_duration,
            backoff_overlapped=tracked.backoff_overlapped,
        ))

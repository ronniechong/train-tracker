"""Live -> coasting -> ghost state machine, stateful across poll cycles -
the layer `merge.py` explicitly defers ("carrying a snapshot forward
across polls ... is the state store's job, layered on top").

Coasting is not a separate trigger, just elapsed time since a trip last had
a live VP position: under `COASTING_TIMEOUT_S` -> "coasting" (keep showing
the last fix), at/over it -> "ghost" (render the scheduled position
instead, handled by station.py once schedule-only rendering lands).

Backoff must not ghost every train: a poller-wide backoff is signalled per
tick via `backoff_active`. While active, NO train's coasting clock
advances - the state machine literally cannot age a live train into
"ghost" during a backoff, rather than relying on a label to suppress it
after the fact. `GhostEvent.backoff_overlapped` records whether backoff
touched the gap at all, for observability, but is not what prevents the
false ghosting - the frozen clock is.

Ghost episodes carry a `reason` (`GhostEvent.reason`): "reappeared" (the
ordinary case), "timed_out" (hit `MAX_GHOST_AGE_S` with no explanation),
"flushed" (process/replay ended mid-episode), or "completed"/"cancelled"
(an independently-confirmed outcome from `completion.py`'s
`TripCompletionTracker`, delivered via `mark_resolved()`). Deliberately
NOT this module's job to detect completion/cancellation itself -- it only
reacts to what `mark_resolved()` is told, same producer/consumer split
`completion.py`'s own docstring describes. A resolved trip that is
currently ghosted is faded immediately; one that is still live/coasting is
remembered (`_resolved`) so that if/when it later would age into "ghost",
it fades with the real reason instead of waiting out `MAX_GHOST_AGE_S`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from .eventlog import EventLog
from .geo import in_city_loop_bbox
from .merge import TrainSnapshot

# Midpoint of the settled "~60-90s" coasting window; matches the
# ghost-eligible threshold `spike/loop_gap_estimate.py` used. First-cut
# constant, revisit like GEOFENCE_RADIUS_M.
COASTING_TIMEOUT_S = 90.0

# A tracked trip untouched by either feed for longer than this is evicted
# outright -- `_trains` itself previously had no eviction, so a trip seen
# only in Trip Updates (never confirmed live, `last_seen_at` staying None
# forever) accumulated in this dict indefinitely, across service days, for
# the life of the process -- hundreds of stale ghosts were observed live
# on the map before this was fixed. Generous relative to real ghost
# durations (typically ~90s-few-minutes); not tuned against eviction load
# specifically.
MAX_GHOST_AGE_S = 2 * 60 * 60

# How long a resolution (completed/cancelled) is remembered for a trip that
# hasn't yet dropped out of live/coasting -- mirrors completion.py's
# FINALIZED_RETENTION_S reasoning: bounds memory for the rare case a
# resolved trip's position keeps appearing (or the resolution turns out to
# be premature/wrong) far longer than a real ghost episode ever would.
RESOLUTION_RETENTION_S = 6 * 60 * 60

Status = Literal["live", "coasting", "ghost"]
Reason = Literal["reappeared", "timed_out", "flushed", "completed", "cancelled"]
Resolution = Literal["completed", "cancelled"]


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
    reason: Reason


@dataclass
class _TrackedTrain:
    status: Status = "live"
    last_seen_at: datetime | None = None
    last_position: tuple[float, float] | None = None
    coasting_elapsed: timedelta = field(default_factory=timedelta)
    backoff_overlapped: bool = False
    ghost_started_at: datetime | None = None
    # Set every tick this trip appears in EITHER feed, regardless of
    # whether it has a live position -- unlike `last_seen_at` (VP-
    # confirmation-only, stays None for TU-only trips), this always gets a
    # real value, so it's what eviction ages against.
    last_touched_at: datetime | None = None


@dataclass(frozen=True)
class TrackedTrainView:
    """Read-only snapshot of one tracked trip, for API/SSE consumers and
    the AI tool layer's evidence fields.

    Deliberately minimal (status + last-known position only) -- this is all
    `_TrackedTrain` actually retains once a trip drops out of both live
    feeds. A vanished/ghost trip's route/schedule fields are NOT available
    here; `merge.py`'s output (`StateStore.latest_snapshots`) is the only
    source for those, and only while the trip is still present in at least
    one feed. A consumer wanting a real "scheduled position" for a fully-
    vanished ghost needs a schedule-lookup, not this."""

    trip_id: str
    status: Status
    last_seen_at: datetime | None
    last_position: tuple[float, float] | None
    # Unlike `last_seen_at` (VP-confirmation-only), always populated once a
    # trip has ticked at least once -- the freshness signal consumers
    # should use for "is this genuinely current" checks (see api/app.py's
    # `_is_current`).
    last_touched_at: datetime | None
    # When status is "ghost", when this ghost episode started -- lets a
    # caller compute a live "ghosted for Xs" duration WHILE still ghosted,
    # not just retroactively via `GhostEvent.ghost_duration_s` at
    # reappearance/eviction. `None` when not currently ghost. Defaulted so
    # existing construction sites (e.g. `test_metrics.py`'s fixture, built
    # before this field existed) don't all need updating for a field they
    # don't exercise.
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
        # trip_id -> (resolution, when recorded) for trips confirmed
        # completed/cancelled by `mark_resolved()` while still live/coasting
        # -- consulted at the moment a trip would otherwise transition into
        # "ghost", not applied retroactively to a trip already showing live.
        self._resolved: dict[str, tuple[Resolution, datetime]] = {}

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
            # Only bump on a genuine feed mention this cycle -- NOT for
            # every already-tracked trip_id (the union above always
            # includes those regardless of whether they reappeared).
            # Setting it unconditionally would mean every ~10s tick
            # refreshes every trip still sitting in `_trains`, and nothing
            # would ever actually age out -- exactly the bug this exists
            # to fix, just reintroduced one level up. Caught before this
            # shipped by tracing through what a real multi-cycle run does.
            if snap is not None:
                tracked.last_touched_at = cycle_time
            position = (snap.latitude, snap.longitude) if snap and snap.has_position else None

            if position is not None:
                if tracked.status == "ghost":
                    self._emit_reappearance(trip_id, tracked, cycle_time, position, reason="reappeared")
                # A resolution recorded while this trip was ghosted/coasting
                # doesn't apply to whatever run reappears under the same
                # trip_id -- clear it rather than risk misclassifying a
                # later, unrelated ghost episode.
                self._resolved.pop(trip_id, None)
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
                resolved = self._resolved.pop(trip_id, None)
                if resolved is not None:
                    self._emit_reappearance(
                        trip_id, tracked, cycle_time, reappear_position=None, reason=resolved[0],
                    )
                    del self._trains[trip_id]
                    continue
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
                resolved = self._resolved.pop(trip_id, None)
                if resolved is not None:
                    self._emit_reappearance(
                        trip_id, tracked, cycle_time, reappear_position=None, reason=resolved[0],
                    )
                    del self._trains[trip_id]
                    continue
                tracked.status = "ghost"
                if not was_ghost:
                    tracked.ghost_started_at = cycle_time
            else:
                tracked.status = "coasting"

        self._evict_stale(cycle_time)

    def _evict_stale(self, cycle_time: datetime) -> None:
        """Drop any trip untouched by either feed for longer than
        `MAX_GHOST_AGE_S`. Closes a still-open ghost episode first (same
        call `flush()` makes) so eviction never silently drops an episode
        from the event log/metrics. Also prunes long-unapplied `_resolved`
        entries (bounded memory, see `RESOLUTION_RETENTION_S`)."""
        stale_ids = [
            trip_id
            for trip_id, tracked in self._trains.items()
            if tracked.last_touched_at is not None
            and (cycle_time - tracked.last_touched_at).total_seconds() >= MAX_GHOST_AGE_S
        ]
        for trip_id in stale_ids:
            tracked = self._trains[trip_id]
            if tracked.status == "ghost":
                self._emit_reappearance(trip_id, tracked, cycle_time, reappear_position=None, reason="timed_out")
            del self._trains[trip_id]
            self._resolved.pop(trip_id, None)

        expired_resolutions = [
            trip_id
            for trip_id, (_, recorded_at) in self._resolved.items()
            if (cycle_time - recorded_at).total_seconds() >= RESOLUTION_RETENTION_S
        ]
        for trip_id in expired_resolutions:
            del self._resolved[trip_id]

    def status_of(self, trip_id: str) -> Status | None:
        tracked = self._trains.get(trip_id)
        return tracked.status if tracked else None

    def view_of(self, trip_id: str) -> TrackedTrainView | None:
        """Single-trip lookup, for the AI tool layer (`ai/tools.py`'s
        `get_trip`) -- same fields `all_tracked()` reports, without
        building a view for every tracked trip just to find one."""
        tracked = self._trains.get(trip_id)
        return self._view(trip_id, tracked) if tracked is not None else None

    def _view(self, trip_id: str, tracked: _TrackedTrain) -> TrackedTrainView:
        return TrackedTrainView(
            trip_id=trip_id,
            status=tracked.status,
            last_seen_at=tracked.last_seen_at,
            last_position=tracked.last_position,
            last_touched_at=tracked.last_touched_at,
            ghost_started_at=tracked.ghost_started_at,
        )

    def all_tracked(self) -> tuple[TrackedTrainView, ...]:
        """Every trip this tracker has ever seen (live, coasting, or ghost),
        not just the ones present in the current cycle's merge output --
        `StateStore.latest_snapshots` alone under-reports ghost/coasting
        trips once they drop out of both feeds entirely: a fully-vanished
        train would otherwise just silently disappear from the API instead
        of being honestly labelled "ghost".

        No age-based filtering *by this method* -- `tick()` itself evicts
        anything untouched for `MAX_GHOST_AGE_S`, so `_trains` is already
        bounded by the time this is called; this method just reports it
        as-is rather than re-filtering."""
        return tuple(self._view(trip_id, tracked) for trip_id, tracked in self._trains.items())

    def flush(self, at: datetime) -> None:
        """Force-close any still-open ghost episodes (e.g. at the end of a
        replay run) so they aren't silently dropped from the event log."""
        for trip_id, tracked in self._trains.items():
            if tracked.status == "ghost":
                self._emit_reappearance(trip_id, tracked, at, reappear_position=None, reason="flushed")

    def mark_resolved(self, trip_id: str, resolution: Resolution, at: datetime) -> None:
        """Called by the orchestrator (`store.py`) when `completion.py`'s
        `TripCompletionTracker` independently confirms a trip's outcome.

        If the trip is currently ghosted, fade it immediately with the real
        reason instead of waiting out `MAX_GHOST_AGE_S` -- a completed or
        cancelled trip's disappearance is explained, not a mystery gap.
        A trip that hasn't ghosted yet (still live/coasting, or not tracked
        at all) is not touched here and now -- doing so would remove a
        train still visibly live on the map, which is out of scope (this
        milestone only shortens ghost episodes, it doesn't change when a
        trip stops being tracked while still visible). The resolution is
        remembered instead, and applied at the moment this trip would
        otherwise transition into "ghost"."""
        tracked = self._trains.get(trip_id)
        if tracked is not None and tracked.status == "ghost":
            self._emit_reappearance(trip_id, tracked, at, reappear_position=None, reason=resolution)
            del self._trains[trip_id]
            return
        self._resolved[trip_id] = (resolution, at)

    def _emit_reappearance(
        self,
        trip_id: str,
        tracked: _TrackedTrain,
        at: datetime,
        reappear_position: tuple[float, float] | None,
        reason: Reason,
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
            reason=reason,
        ))

"""Trip-completion tracking: classifies each trip on_time/late once it
genuinely reaches its terminus, per the official Victorian punctuality
definition (4:59 threshold, arrival AT THE TERMINUS -- not "how delayed is
this trip right now").

Deliberately separate from `TrainLifecycleTracker` (ghost.py): that state
machine answers "is this trip currently visible", this one answers "did
this trip finish, and was it on time" -- a trip can ghost mid-journey and
still later complete, and a trip can complete without ever having ghosted.
Sibling module sharing the `EventLog` seam, not a shared class.

Terminus-arrival detection reuses station.py's exact "genuinely absent
field, not list position" rule for the rolling-window `stop_time_update`
list (CLAUDE.md's documented data-source fact): a stop counts as the
GENUINE terminus only once TU reports it with an arrival value and NO
departure value -- the same signal station.py uses to render "at genuine
terminus" for live station state, applied here to detect trip *completion*
instead.

By design: a trip that drops off tracking before reaching its terminus
(coverage gap, ghost-timeout) still gets an honest `undetermined_gap`
event recorded here -- the internal event log stays gap-honest regardless
of what any consuming digest chooses to surface.

Reliability vs. punctuality: comparable transport authorities publish
these as two SEPARATE contractual metrics, not one -- reliability asks
"did the trip happen at all" (cancellations count against THIS, not
punctuality); punctuality asks "of the trips that DID run, how many
arrived within the threshold". A cancelled trip is therefore its own
`cancelled` status here, finalized immediately off TU's
`schedule_relationship`, never left to fall through to
`undetermined_gap`'s two-hour timeout (we already know its outcome) and
never scored as on_time/late (it has no real arrival to measure).

Known, deliberately out-of-scope gap: a trip present in the day's static
schedule that never appears in the realtime feed AT ALL (no TU, no VP, not
even a CANCELED entity) is invisible to this tracker entirely -- it is
never registered, so it silently doesn't affect punctuality OR the
`cancelled` bucket. Detecting that case needs enumerating the full day's
scheduled trips from the static snapshot and diffing against everything
actually seen, a distinct, comparable-effort feature (a true reliability
tracker) that hasn't been scoped or built. Flagged here rather than left
implicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Literal

from .eventlog import EventLog
from .merge import TrainSnapshot

# PTV's public MR4 franchise-contract threshold -- reused deliberately so
# train-tracker's own "on time" means what Melburnians already associate
# with that word.
ON_TIME_THRESHOLD_S = 299  # 4 minutes 59 seconds

# A pending trip untouched by a fresh TU schedule for longer than this is
# presumed lost to a coverage gap rather than still in progress. Generous
# relative to any real Metro trip's run time (mirrors ghost.py's
# MAX_GHOST_AGE_S reasoning); first-cut constant, revisit once real data
# exists, same convention as GEOFENCE_RADIUS_M/COASTING_TIMEOUT_S.
UNDETERMINED_TIMEOUT_S = 2 * 60 * 60

# How long a finalized trip_id is remembered purely to guard against a
# double-emission (see `_finalized` below). Bounded by wall-clock age, not
# service_date: trips being processed in the same tick can legitimately
# carry DIFFERENT individual service_dates near a day boundary (some still
# on yesterday's 24:xx-encoded schedule, others already on today's), so
# rotating this off a per-trip service_date would thrash every tick rather
# than track real calendar time.
FINALIZED_RETENTION_S = 6 * 60 * 60

Status = Literal["on_time", "late", "cancelled", "undetermined_gap"]

# trip_id, its own service_date (per TU's `trip.start_date`) -> terminus info.
TerminusLookup = Callable[[str, date], "TripTerminus | None"]


@dataclass(frozen=True)
class TripTerminus:
    stop_id: str
    scheduled_arrival: datetime  # absolute UTC


@dataclass(frozen=True)
class TripCompletionEvent:
    trip_id: str
    route_id: str | None
    service_date: str  # ISO date
    scheduled_terminus_arrival: datetime
    actual_terminus_arrival: datetime | None  # None for cancelled/undetermined_gap
    delay_seconds: int | None  # None for cancelled/undetermined_gap
    status: Status


@dataclass
class _PendingTrip:
    terminus_stop_id: str
    scheduled_arrival: datetime
    route_id: str | None
    service_date: date
    last_touched_at: datetime


def _parse_start_date(value: str) -> date:
    """TU's `trip.start_date` is GTFS-RT's own "YYYYMMDD" string -- kept as
    a small local parser (not importing gtfs/calendar.py's private
    equivalent) matching station.py's precedent of a tiny local helper over
    cross-module coupling for a one-line format."""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


class TripCompletionTracker:
    def __init__(self, event_log: EventLog, terminus_lookup: TerminusLookup):
        self._event_log = event_log
        self._terminus_lookup = terminus_lookup
        self._pending: dict[str, _PendingTrip] = {}
        # Trip_ids finalized (completed or gapped) recently -- guards
        # against a re-registration double-emitting if a trip_id somehow
        # reappears in TU after its completion event already fired (not
        # expected in practice, but cheap to guard given DiscrepancyEvent's
        # own edge-triggering already had to learn this lesson once).
        # trip_id -> the cycle_time it was finalized at; pruned by age in
        # `_evict_stale` rather than tied to a service_date boundary.
        self._finalized: dict[str, datetime] = {}

    def tick(
        self,
        snapshots: dict[str, TrainSnapshot],
        cycle_time: datetime,
        undetermined_timeout: timedelta = timedelta(seconds=UNDETERMINED_TIMEOUT_S),
    ) -> None:
        for trip_id, snapshot in snapshots.items():
            if not snapshot.has_schedule or snapshot.start_date is None:
                # No fresh TU data this cycle -- nothing new to observe
                # about this trip's terminus (VP-only cycles are normal,
                # not a gap by themselves; `last_touched_at` simply doesn't
                # advance, same as ghost.py's touch semantics).
                continue

            try:
                service_date = _parse_start_date(snapshot.start_date)
            except (ValueError, IndexError):
                continue  # malformed start_date -- can't resolve a service_date, skip honestly

            pending = self._pending.get(trip_id)
            if pending is None:
                if trip_id in self._finalized:
                    continue
                terminus = self._terminus_lookup(trip_id, service_date)
                if terminus is None:
                    # No static schedule for this trip (e.g. a real-time-
                    # only ADDED trip) -- nothing to compare against, so
                    # this trip is simply never tracked for completion.
                    continue
                pending = _PendingTrip(
                    terminus_stop_id=terminus.stop_id,
                    scheduled_arrival=terminus.scheduled_arrival,
                    route_id=snapshot.route_id,
                    service_date=service_date,
                    last_touched_at=cycle_time,
                )
                self._pending[trip_id] = pending
            else:
                pending.last_touched_at = cycle_time
                if snapshot.route_id is not None:
                    pending.route_id = snapshot.route_id

            if snapshot.schedule_relationship == "CANCELED":
                # Reliability, not punctuality -- a cancellation is a KNOWN
                # outcome, distinct from "we lost coverage, don't know what
                # happened" (undetermined_gap). Finalized immediately rather
                # than left to time out after UNDETERMINED_TIMEOUT_S: we
                # already have the answer, no reason to wait two hours to
                # record it. Matches the reliability/punctuality split every
                # comparable transport authority's published methodology
                # uses.
                self._finalize(
                    trip_id, pending, cycle_time,
                    status="cancelled", actual_arrival=None, delay_seconds=None,
                )
                continue

            terminus_stu = next(
                (stu for stu in snapshot.stop_time_updates if stu.stop_id == pending.terminus_stop_id),
                None,
            )
            if (
                terminus_stu is not None
                and terminus_stu.arrival_time is not None
                and terminus_stu.departure_time is None
            ):
                # `StopTimeUpdate.arrival_time` is typed `int | None` but is
                # actually a string at runtime -- protobuf's JSON mapping
                # stringifies int64 fields (arrival_delay is int32, stays a
                # real number). Same coercion station.py's `_epoch()` and
                # api/app.py's `_scheduled_train` already apply for the
                # identical reason -- every test fixture used a real int, so
                # this needs the explicit cast rather than trusting the type
                # hint.
                actual_arrival = datetime.fromtimestamp(int(terminus_stu.arrival_time), tz=timezone.utc)
                delay = (
                    terminus_stu.arrival_delay
                    if terminus_stu.arrival_delay is not None
                    else int((actual_arrival - pending.scheduled_arrival).total_seconds())
                )
                self._finalize(
                    trip_id, pending, cycle_time,
                    status="on_time" if delay <= ON_TIME_THRESHOLD_S else "late",
                    actual_arrival=actual_arrival, delay_seconds=delay,
                )

        self._evict_stale(cycle_time, undetermined_timeout)

    def _evict_stale(self, cycle_time: datetime, timeout: timedelta) -> None:
        stale_ids = [
            trip_id
            for trip_id, pending in self._pending.items()
            if (cycle_time - pending.last_touched_at) >= timeout
        ]
        for trip_id in stale_ids:
            pending = self._pending[trip_id]
            self._finalize(
                trip_id, pending, cycle_time,
                status="undetermined_gap", actual_arrival=None, delay_seconds=None,
            )

        retention = timedelta(seconds=FINALIZED_RETENTION_S)
        expired = [
            trip_id for trip_id, finalized_at in self._finalized.items()
            if (cycle_time - finalized_at) >= retention
        ]
        for trip_id in expired:
            del self._finalized[trip_id]

    def flush(self, at: datetime) -> None:
        """Force-close every still-pending trip as `undetermined_gap` (e.g.
        at the end of a replay run) so none are silently dropped."""
        for trip_id, pending in list(self._pending.items()):
            self._finalize(
                trip_id, pending, at,
                status="undetermined_gap", actual_arrival=None, delay_seconds=None,
            )

    def _finalize(
        self,
        trip_id: str,
        pending: _PendingTrip,
        finalized_at: datetime,
        status: Status,
        actual_arrival: datetime | None,
        delay_seconds: int | None,
    ) -> None:
        self._event_log.record(TripCompletionEvent(
            trip_id=trip_id,
            route_id=pending.route_id,
            service_date=pending.service_date.isoformat(),
            scheduled_terminus_arrival=pending.scheduled_arrival,
            actual_terminus_arrival=actual_arrival,
            delay_seconds=delay_seconds,
            status=status,
        ))
        del self._pending[trip_id]
        self._finalized[trip_id] = finalized_at

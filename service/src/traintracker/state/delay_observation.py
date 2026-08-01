"""Delay observation logging for the delay/ETA-prediction feature
(05-ai-layer, scoped 2026-08-01 -- see milestones/05-ai-layer.md's
"Delay/ETA prediction" section for the locked pipeline design).

This is step one of that feature, not the model: `HistoryStore` today
only ever persists trip *completion* outcomes (a single delay value at
the terminus), never a time series of mid-journey delay observations --
exactly the training features the eventual model needs (current delay,
stops remaining, time of day, active-alert flag) don't exist in any
historical record yet. This tracker is what starts collecting them.

Deliberately separate from `TripCompletionTracker` (sibling, same
`EventLog` seam, not a shared class -- same precedent as `completion.py`
vs `ghost.py`): that tracker answers "did this trip finish, and was it
on time" once, at completion; this one answers "what did this trip's
delay look like at various points along the way," repeatedly, while
it's still in progress. A trip is observed by both trackers over its
lifetime, independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from ..gtfs.schedule_cache import TripTerminus
from .alerts import Alert, alerts_matching
from .eventlog import EventLog
from .merge import TrainSnapshot

# Proposed default (milestones/05-ai-layer.md): 10s-cycle logging would be
# enormous (~200 concurrent trips x 6/min x 15h service >= 1M+ rows/day).
# Revisit once real concurrent-trip counts are measured against this.
OBSERVATION_INTERVAL_S = 120

# How long a trip_id is remembered purely to pace its own observation
# cadence -- bounded by wall-clock age, same reasoning as completion.py's
# FINALIZED_RETENTION_S (a trip not touched this long is long finished;
# forgetting it is just bookkeeping hygiene, not a correctness concern).
LAST_OBSERVED_RETENTION_S = 6 * 60 * 60

# trip_id, its own service_date -> the trip's static terminus (needs
# stop_sequence, unlike completion.py's own narrower TerminusLookup --
# hence importing schedule_cache's TripTerminus directly rather than
# duck-typing against completion.py's smaller one).
TerminusLookup = Callable[[str, date], "TripTerminus | None"]


@dataclass(frozen=True)
class DelayObservationEvent:
    trip_id: str
    route_id: str | None
    service_date: str  # ISO date
    observed_at: datetime
    current_delay_s: int
    stops_remaining: int
    active_alert_flag: bool


def _parse_start_date(value: str) -> date:
    """Mirrors completion.py's own tiny local parser for TU's `trip.
    start_date` -- same one-line-format, no cross-module coupling
    precedent as that module's own `_parse_start_date`."""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


class DelayObservationTracker:
    def __init__(
        self,
        event_log: EventLog,
        terminus_lookup: TerminusLookup,
        observation_interval: timedelta = timedelta(seconds=OBSERVATION_INTERVAL_S),
    ):
        self._event_log = event_log
        self._terminus_lookup = terminus_lookup
        self._observation_interval = observation_interval
        self._last_observed: dict[str, datetime] = {}

    def tick(
        self,
        snapshots: dict[str, TrainSnapshot],
        cycle_time: datetime,
        latest_alerts: dict[str, Alert],
    ) -> None:
        for trip_id, snapshot in snapshots.items():
            if not snapshot.has_schedule or snapshot.start_date is None:
                continue
            if snapshot.schedule_relationship == "CANCELED":
                # A cancelled trip has no meaningful "current delay" to
                # observe -- same reliability/punctuality exclusion
                # completion.py applies, kept consistent here.
                continue
            if not snapshot.stop_time_updates:
                continue  # no signal to observe from this cycle

            last = self._last_observed.get(trip_id)
            if last is not None and (cycle_time - last) < self._observation_interval:
                continue

            try:
                service_date = _parse_start_date(snapshot.start_date)
            except (ValueError, IndexError):
                continue  # malformed start_date -- can't resolve a service_date, skip honestly

            terminus = self._terminus_lookup(trip_id, service_date)
            if terminus is None:
                # No static schedule for this trip (e.g. a real-time-only
                # ADDED trip) -- nothing to compute stops_remaining
                # against, so this trip is simply never observed.
                continue

            # The rolling window trims departed stops off the front (see
            # CLAUDE.md's documented stop_time_update behaviour) -- its
            # lowest stop_sequence is the nearest stop not yet complete,
            # the natural anchor for "current delay" and "stops remaining."
            nearest_stu = min(snapshot.stop_time_updates, key=lambda stu: stu.stop_sequence)
            delay = (
                nearest_stu.arrival_delay
                if nearest_stu.arrival_delay is not None
                else nearest_stu.departure_delay
            )
            if delay is None:
                continue  # no delay signal on the nearest stop -- skip rather than fabricate

            stops_remaining = terminus.stop_sequence - nearest_stu.stop_sequence
            if stops_remaining < 0:
                # Rolling-window/terminus mismatch edge case (shouldn't
                # happen in practice) -- skip rather than record a
                # negative feature a future model would have to special-case.
                continue

            active_alert_flag = bool(
                alerts_matching(latest_alerts, cycle_time, route_id=snapshot.route_id)
            ) if snapshot.route_id is not None else False

            self._event_log.record(DelayObservationEvent(
                trip_id=trip_id,
                route_id=snapshot.route_id,
                service_date=service_date.isoformat(),
                observed_at=cycle_time,
                current_delay_s=delay,
                stops_remaining=stops_remaining,
                active_alert_flag=active_alert_flag,
            ))
            self._last_observed[trip_id] = cycle_time

        self._evict_stale(cycle_time)

    def _evict_stale(self, cycle_time: datetime) -> None:
        retention = timedelta(seconds=LAST_OBSERVED_RETENTION_S)
        stale_ids = [
            trip_id for trip_id, last in self._last_observed.items()
            if (cycle_time - last) >= retention
        ]
        for trip_id in stale_ids:
            del self._last_observed[trip_id]

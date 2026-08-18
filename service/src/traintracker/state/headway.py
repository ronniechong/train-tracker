"""Headway/frequency tracking: builds a per-(stop, route, direction) rolling
history of recent arrivals purely from the poller's own in-memory feed
history, so "how far apart do trains actually run here" and "is a gap
forming right now" can be answered without a live SQLite query or a
periodic cached job.

Grouped by (stop_id, route_id, direction_id) rather than stop_id alone: a
station served by multiple lines (or one line running both directions)
would otherwise blend unrelated headways into a meaningless average.

Arrival detection reuses station.py's `derive_station_state` (the same
schedule-derived "at" signal every other M12 feature relies on) rather than
re-deriving "is this train at this stop right now" from raw stop_time
updates a second time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from .merge import TrainSnapshot
from .station import derive_station_state

# Last N arrivals per group, not a time window -- a fixed sample size keeps
# the average meaningful across both quiet and busy periods without needing
# to reason about how far back "recent" should mean.
MAX_ARRIVALS_PER_GROUP = 6

# A single historical gap (2 arrivals) is one data point, not an average --
# far too noisy to flag a live wait as anomalous against. Three arrivals
# (two historical gaps) is the minimum that lets the average itself mean
# anything before it's used to judge a third, live gap.
MIN_ARRIVALS_FOR_GAP_DETECTION = 3

# A trip's dwell can span several poll cycles; without this a trip
# dwelling for 3 consecutive ticks would otherwise record 3 arrivals
# instead of 1. Bounded by wall-clock age, same reasoning as
# delay_observation.py's LAST_OBSERVED_RETENTION_S -- a trip not touched
# this long is long gone, forgetting it is bookkeeping hygiene only.
RECORDED_DWELL_RETENTION_S = 6 * 60 * 60

GroupKey = tuple[str, str, int]  # stop_id, route_id, direction_id

# trip_id, its own service_date (per TU's `trip.start_date`) -> direction_id,
# same shape/convention as completion.py's/delay_observation.py's own
# TerminusLookup -- resolved from the static schedule, never imported
# directly from schedule_cache here.
DirectionLookup = Callable[[str, date], "int | None"]


@dataclass(frozen=True)
class HeadwayInfo:
    average_headway_seconds: float | None
    sample_size: int
    seconds_since_last_arrival: int | None
    gap_detected: bool


def _parse_start_date(value: str) -> date:
    """Mirrors completion.py's/delay_observation.py's own tiny local
    parser for TU's `trip.start_date`."""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def compute_headway_info(arrivals: "deque[datetime]", now: datetime) -> HeadwayInfo:
    """Pure function: a group's rolling arrival buffer + `now` ->
    `HeadwayInfo`. Null fields when there's insufficient data to answer
    honestly, same convention as station.py/completion.py."""
    sample_size = len(arrivals)
    if sample_size == 0:
        return HeadwayInfo(
            average_headway_seconds=None, sample_size=0,
            seconds_since_last_arrival=None, gap_detected=False,
        )

    seconds_since_last = int((now - arrivals[-1]).total_seconds())

    if sample_size < 2:
        return HeadwayInfo(
            average_headway_seconds=None, sample_size=sample_size,
            seconds_since_last_arrival=seconds_since_last, gap_detected=False,
        )

    ordered = list(arrivals)
    gaps = [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:])]
    average = sum(gaps) / len(gaps)

    gap_detected = (
        sample_size >= MIN_ARRIVALS_FOR_GAP_DETECTION
        and average > 0
        and seconds_since_last > 2 * average
    )

    return HeadwayInfo(
        average_headway_seconds=average, sample_size=sample_size,
        seconds_since_last_arrival=seconds_since_last, gap_detected=gap_detected,
    )


class HeadwayTracker:
    def __init__(self, direction_lookup: DirectionLookup):
        self._direction_lookup = direction_lookup
        self._arrivals: dict[GroupKey, "deque[datetime]"] = {}
        # trip_id -> (stop_id it's currently recorded as dwelling at, the
        # cycle_time it was last confirmed still dwelling there) -- the
        # edge-triggering guard against recording the same physical
        # arrival once per poll cycle, same `_finalized`/
        # `_active_discrepancies` pattern used elsewhere in state/.
        self._recorded_dwell: dict[str, tuple[str, datetime]] = {}

    def tick(
        self,
        snapshots: dict[str, TrainSnapshot],
        now: datetime,
        stops: dict | None = None,
    ) -> None:
        stops = stops or {}
        for trip_id, snapshot in snapshots.items():
            if not snapshot.has_schedule or snapshot.start_date is None or snapshot.route_id is None:
                continue

            state = derive_station_state(snapshot, stops, now)
            recorded = self._recorded_dwell.get(trip_id)

            if state.status != "at" or state.at_stop_id is None:
                if recorded is not None:
                    del self._recorded_dwell[trip_id]
                continue

            if recorded is not None and recorded[0] == state.at_stop_id:
                # Still dwelling at the same stop already recorded this
                # episode -- refresh the touch time, don't record again.
                self._recorded_dwell[trip_id] = (state.at_stop_id, now)
                continue

            try:
                service_date = _parse_start_date(snapshot.start_date)
            except (ValueError, IndexError):
                continue  # malformed start_date -- can't resolve a service_date, skip honestly

            direction_id = self._direction_lookup(trip_id, service_date)
            if direction_id is None:
                # No static schedule to resolve a direction from (e.g. a
                # real-time-only ADDED trip) -- can't group this arrival
                # without blending directions, so it's simply never
                # recorded.
                continue

            key = (state.at_stop_id, snapshot.route_id, direction_id)
            self._arrivals.setdefault(key, deque(maxlen=MAX_ARRIVALS_PER_GROUP)).append(now)
            self._recorded_dwell[trip_id] = (state.at_stop_id, now)

        self._evict_stale(now)

    def _evict_stale(self, now: datetime) -> None:
        retention = timedelta(seconds=RECORDED_DWELL_RETENTION_S)
        stale_ids = [
            trip_id for trip_id, (_, last_touched) in self._recorded_dwell.items()
            if (now - last_touched) >= retention
        ]
        for trip_id in stale_ids:
            del self._recorded_dwell[trip_id]

    def headway_for(self, stop_id: str, route_id: str, direction_id: int, now: datetime) -> HeadwayInfo:
        arrivals = self._arrivals.get((stop_id, route_id, direction_id))
        if arrivals is None:
            return HeadwayInfo(
                average_headway_seconds=None, sample_size=0,
                seconds_since_last_arrival=None, gap_detected=False,
            )
        return compute_headway_info(arrivals, now)

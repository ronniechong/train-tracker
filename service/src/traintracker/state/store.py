"""Ties `merge()` and `TrainLifecycleTracker` together into the one thing a
poller loop (2b) or a replay harness actually calls once per feed refresh.

Station-state derivation is deliberately NOT folded in here: it's a pure
function of a single snapshot + `now` + `stops`, with no cross-cycle memory,
so any caller (this store, a future API layer, tests) can call it directly
on whatever snapshot it already has - no reason to route it through this
stateful object too.
"""

from __future__ import annotations

from datetime import datetime

from .eventlog import EventLog
from .ghost import TrainLifecycleTracker
from .merge import TrainSnapshot, merge


class StateStore:
    def __init__(self, discrepancy_log: EventLog, ghost_log: EventLog):
        self._discrepancy_log = discrepancy_log
        self._lifecycle = TrainLifecycleTracker(ghost_log)
        self.latest_snapshots: dict[str, TrainSnapshot] = {}
        # (trip_id, discrepancy_type) pairs active as of the last ingest.
        # `merge()` is a deliberately memoryless, single-cycle function (see
        # its own docstring), so a discrepancy that persists across many
        # re-merges - e.g. a trip in VP with no TU match for several
        # minutes - would otherwise be logged once per cycle instead of
        # once per episode (confirmed against the real replay fixture: one
        # persistent mismatch logged 500+ times). Edge-triggering here, not
        # in merge(), keeps merge() a simple pure function.
        self._active_discrepancies: set[tuple[str, str]] = set()

    def ingest(
        self, tu_feed: dict, vp_feed: dict, cycle_time: datetime, backoff_active: bool = False,
    ) -> dict[str, TrainSnapshot]:
        snapshots, discrepancies = merge(tu_feed, vp_feed)

        current = {(d.trip_id, d.discrepancy_type) for d in discrepancies}
        for discrepancy in discrepancies:
            key = (discrepancy.trip_id, discrepancy.discrepancy_type)
            if key not in self._active_discrepancies:
                self._discrepancy_log.record(discrepancy)
        self._active_discrepancies = current

        self._lifecycle.tick(snapshots, cycle_time, backoff_active=backoff_active)
        self.latest_snapshots = snapshots
        return snapshots

    def status_of(self, trip_id: str):
        return self._lifecycle.status_of(trip_id)

    def flush(self, at: datetime) -> None:
        self._lifecycle.flush(at)

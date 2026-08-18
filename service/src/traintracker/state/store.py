"""Ties `merge()` and `TrainLifecycleTracker` together into the one thing a
poller loop or a replay harness actually calls once per feed refresh.

Station-state derivation is deliberately NOT folded in here: it's a pure
function of a single snapshot + `now` + `stops`, with no cross-cycle memory,
so any caller (this store, a future API layer, tests) can call it directly
on whatever snapshot it already has - no reason to route it through this
stateful object too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .alerts import Alert, parse_alerts
from .completion import TripCompletionTracker
from .delay_observation import DelayObservationTracker
from .eventlog import EventLog
from .ghost import TrackedTrainView, TrainLifecycleTracker
from .headway import HeadwayInfo, HeadwayTracker
from .merge import TrainSnapshot, merge


class StateStore:
    def __init__(
        self,
        discrepancy_log: EventLog,
        ghost_log: EventLog,
        on_tick: Callable[[tuple[TrackedTrainView, ...]], None] | None = None,
        completion_tracker: TripCompletionTracker | None = None,
        delay_observation_tracker: DelayObservationTracker | None = None,
        headway_tracker: HeadwayTracker | None = None,
    ):
        self._discrepancy_log = discrepancy_log
        self._lifecycle = TrainLifecycleTracker(ghost_log)
        # Optional (trip-completion tracking): a fresh StateStore built
        # without a static-schedule-backed terminus lookup (e.g. most
        # existing tests) simply never tracks completions -- every other
        # feature keeps working unchanged, matching `on_tick`'s own
        # optional-hook precedent.
        self._completion_tracker = completion_tracker
        # Optional (delay/ETA-prediction observation logging): same
        # optional-hook convention as completion_tracker above. Needs
        # `self.latest_alerts` for its active_alert_flag feature, which is
        # why it's ticked from inside ingest() (below) rather than by a
        # separate caller -- that's the one place both a fresh `snapshots`
        # and a fresh `latest_alerts` are in hand together at the same
        # cycle_time.
        self._delay_observation_tracker = delay_observation_tracker
        # Optional (headway/frequency from history): same optional-hook
        # convention as the trackers above.
        self._headway_tracker = headway_tracker
        # Optional hook fired with the fresh `all_tracked()` result after
        # every `ingest()` -- lets a caller (e.g. metrics) observe tracked-
        # trip counts without this module knowing metrics exist at all,
        # same separation `merge.py`/`ghost.py` already keep.
        self._on_tick = on_tick
        self.latest_snapshots: dict[str, TrainSnapshot] = {}
        # Replaced wholesale on each `sa_feed`-bearing ingest, not merged
        # incrementally - the SA feed itself is a full current-alert
        # snapshot each poll (see alerts.py), so this store just mirrors
        # that, same as `latest_snapshots` mirrors the latest TU/VP merge.
        self.latest_alerts: dict[str, Alert] = {}
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
        sa_feed: dict | None = None,
    ) -> dict[str, TrainSnapshot]:
        snapshots, discrepancies = merge(tu_feed, vp_feed)

        # `None` means "no SA content cached yet this process" (poller
        # hasn't completed a first successful SA poll) - distinct from an
        # actually-empty feed, which legitimately means zero active
        # alerts. Only the former should leave `latest_alerts` untouched.
        if sa_feed is not None:
            self.latest_alerts = parse_alerts(sa_feed)

        current = {(d.trip_id, d.discrepancy_type) for d in discrepancies}
        for discrepancy in discrepancies:
            key = (discrepancy.trip_id, discrepancy.discrepancy_type)
            if key not in self._active_discrepancies:
                self._discrepancy_log.record(discrepancy)
        self._active_discrepancies = current

        self._lifecycle.tick(snapshots, cycle_time, backoff_active=backoff_active)
        if self._completion_tracker is not None:
            finalized = self._completion_tracker.tick(snapshots, cycle_time)
            # Feed each fresh completion/cancellation into the ghost tracker
            # so a resolved trip fades on its real reason instead of
            # waiting out `ghost.py`'s MAX_GHOST_AGE_S timeout.
            # `undetermined_gap` is deliberately not forwarded -- it means
            # completion.py itself lost the trip to a coverage gap, not a
            # known outcome, so ghost.py's own timeout should still govern.
            for event in finalized:
                if event.status == "cancelled":
                    self._lifecycle.mark_resolved(event.trip_id, "cancelled", cycle_time)
                elif event.status in ("on_time", "late"):
                    self._lifecycle.mark_resolved(event.trip_id, "completed", cycle_time)
        if self._delay_observation_tracker is not None:
            self._delay_observation_tracker.tick(snapshots, cycle_time, self.latest_alerts)
        if self._headway_tracker is not None:
            self._headway_tracker.tick(snapshots, cycle_time)
        self.latest_snapshots = snapshots
        if self._on_tick is not None:
            self._on_tick(self._lifecycle.all_tracked())
        return snapshots

    def status_of(self, trip_id: str):
        return self._lifecycle.status_of(trip_id)

    def view_of(self, trip_id: str) -> TrackedTrainView | None:
        return self._lifecycle.view_of(trip_id)

    def all_tracked(self) -> tuple[TrackedTrainView, ...]:
        return self._lifecycle.all_tracked()

    def headway_for(
        self, stop_id: str, route_id: str, direction_id: int, now: datetime,
    ) -> HeadwayInfo | None:
        """None when no headway_tracker is configured -- distinct from
        `HeadwayInfo` with a zero sample_size, which means the tracker IS
        running but hasn't seen an arrival for this group yet."""
        if self._headway_tracker is None:
            return None
        return self._headway_tracker.headway_for(stop_id, route_id, direction_id, now)

    def flush(self, at: datetime) -> None:
        self._lifecycle.flush(at)
        if self._completion_tracker is not None:
            self._completion_tracker.flush(at)

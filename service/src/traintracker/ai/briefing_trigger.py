"""Cheap, LLM-free trigger check for 05e's disruption briefings -- run once
per poll cycle (poller/__main__.py), watching only signals already sitting
in `StateStore` for free: a new Service Alert, an existing alert's effect
escalating, or newly-cancelled trips crossing a threshold within a rolling
window. Only a materially-changed network state AND a minimum cooldown
since the last SENT briefing justifies an LLM call -- the SQLite budget cap
(ai/budget.py) is the backstop, not the primary control
(milestones/05-ai-layer.md).

Threshold/window/cooldown values below are first-cut, not measured against
real disruption frequency -- 05a's schedule_relationship/alert parsing
landed the data source hours before this module was written, so there has
been no soak period yet. Revisit once real numbers exist, same "first-cut
constants, revisit at soak gate" convention as GEOFENCE_RADIUS_M/
COASTING_TIMEOUT_S (CLAUDE.md's settled-decisions table).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..state.alerts import is_active
from ..state.store import StateStore

CANCELLATION_WINDOW_S = 900.0  # 15 min
CANCELLATION_THRESHOLD = 3
COOLDOWN_S = 1800.0  # 30 min

# GTFS-RT spec's Alert.Effect enum, ranked worst (0) to least severe -- a
# heuristic ordering, not measured against this feed's real distribution
# yet. A value not listed here (one this feed has never been observed to
# send) ranks below every named value, never above -- an unrecognised
# effect string must never look more urgent than a known-bad one.
_EFFECT_SEVERITY = {
    "NO_SERVICE": 0,
    "REDUCED_SERVICE": 1,
    "SIGNIFICANT_DELAYS": 2,
    "DETOUR": 3,
    "MODIFIED_SERVICE": 4,
    "STOP_MOVED": 5,
    "OTHER_EFFECT": 6,
    "ADDITIONAL_SERVICE": 7,
    "NO_EFFECT": 8,
}
_UNKNOWN_EFFECT_SEVERITY = len(_EFFECT_SEVERITY)


def _severity(effect: str | None) -> int:
    if effect is None:
        return _UNKNOWN_EFFECT_SEVERITY
    return _EFFECT_SEVERITY.get(effect, _UNKNOWN_EFFECT_SEVERITY)


@dataclass(frozen=True)
class TriggerReason:
    kind: str  # "new_alert" | "alert_escalated" | "cancellation_threshold"
    detail: str  # human-readable, fed into the briefing's user prompt


class BriefingTrigger:
    """Stateful by design -- `StateStore` deliberately keeps no memory of
    "what did the previous cycle look like" beyond its own edge-triggering
    for discrepancies, so this module carries its own small "what have I
    already told the model about" state, one instance per running poller.

    Known simplification: a trigger condition suppressed by an active
    cooldown is not queued or retried later -- if nothing about the
    network changes again before the next real trigger, that disruption
    never gets a briefing. Accepted for this pass rather than building a
    deferred-retry mechanism ahead of any real usage data showing it's
    actually needed."""

    def __init__(
        self,
        cancellation_window_s: float = CANCELLATION_WINDOW_S,
        cancellation_threshold: int = CANCELLATION_THRESHOLD,
        cooldown_s: float = COOLDOWN_S,
    ):
        self._cancellation_window_s = cancellation_window_s
        self._cancellation_threshold = cancellation_threshold
        self._cooldown_s = cooldown_s
        self._seen_alert_effects: dict[str, str | None] = {}
        self._seen_cancelled_trip_ids: set[str] = set()
        self._recent_cancellations: deque[datetime] = deque()
        self._last_briefed_at: datetime | None = None

    def record_briefed(self, now: datetime) -> None:
        """Called only after a briefing actually SENDS, never on trigger
        alone -- a failed or budget-blocked attempt must not silently
        reset the cooldown and swallow a real disruption."""
        self._last_briefed_at = now

    def evaluate(self, store: StateStore, now: datetime) -> TriggerReason | None:
        # Both checks run unconditionally, every call -- each does its own
        # "what's new since last cycle" bookkeeping, which must stay
        # correct even on a cycle where the OTHER check already found a
        # reason (short-circuiting here would silently stop tracking
        # cancellations the moment an alert fires, or vice versa).
        alert_reason = self._check_alerts(store, now)
        cancellation_reason = self._check_cancellations(store, now)
        reason = alert_reason or cancellation_reason
        if reason is None:
            return None
        if (
            self._last_briefed_at is not None
            and (now - self._last_briefed_at).total_seconds() < self._cooldown_s
        ):
            return None
        return reason

    def _check_alerts(self, store: StateStore, now: datetime) -> TriggerReason | None:
        reason: TriggerReason | None = None
        current: dict[str, str | None] = {}
        for alert_id, alert in store.latest_alerts.items():
            if not is_active(alert, now):
                continue
            current[alert_id] = alert.effect
            if alert_id not in self._seen_alert_effects:
                if reason is None:
                    reason = TriggerReason("new_alert", alert.header_text or alert_id)
            elif _severity(alert.effect) < _severity(self._seen_alert_effects[alert_id]):
                if reason is None:
                    reason = TriggerReason(
                        "alert_escalated",
                        f"{alert.header_text or alert_id}: "
                        f"{self._seen_alert_effects[alert_id]} -> {alert.effect}",
                    )
        self._seen_alert_effects = current
        return reason

    def _check_cancellations(self, store: StateStore, now: datetime) -> TriggerReason | None:
        current_cancelled = {
            trip_id
            for trip_id, snapshot in store.latest_snapshots.items()
            if snapshot.schedule_relationship == "CANCELED"
        }
        newly_cancelled = current_cancelled - self._seen_cancelled_trip_ids
        self._seen_cancelled_trip_ids = current_cancelled

        for _ in newly_cancelled:
            self._recent_cancellations.append(now)
        cutoff = now - timedelta(seconds=self._cancellation_window_s)
        while self._recent_cancellations and self._recent_cancellations[0] < cutoff:
            self._recent_cancellations.popleft()

        if len(self._recent_cancellations) >= self._cancellation_threshold:
            return TriggerReason(
                "cancellation_threshold",
                f"{len(self._recent_cancellations)} cancellations in the last "
                f"{int(self._cancellation_window_s // 60)} min",
            )
        return None

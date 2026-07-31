"""Cheap, LLM-free gate for `POST /briefing/trigger` (api/app.py): decides
whether there's enough real signal in the currently active Service Alerts
to make an LLM call worth its cost at all.

Built off a concrete, observed failure mode (2026-08-01): several real
on-demand briefings produced nothing useful -- "Unable to complete
briefing... the alert metadata doesn't include the affected line" and
generic "unknown route/stops" filler -- because the active alert(s) carried
no `informed_entity` with a real `route_id` at all. Haiku correctly
refused/hedged in each case, but only after a paid call. This check catches
that exact class of alert BEFORE spending anything, not after.

Deliberately narrower than `state/alerts.py`'s own "no informed_entity
means network-wide, matches everything" semantics -- that reading is
correct for filtering/matching alerts against a line, but wrong here: an
alert with no route_id anywhere gives a briefing writer nothing to name,
regardless of how broadly it might apply.
"""

from __future__ import annotations

from datetime import datetime

from ..state.alerts import is_active
from ..state.store import StateStore


def has_briefable_alerts(store: StateStore, now: datetime) -> bool:
    for alert in store.latest_alerts.values():
        if not is_active(alert, now):
            continue
        if any(entity.route_id is not None for entity in alert.informed_entities):
            return True
    return False

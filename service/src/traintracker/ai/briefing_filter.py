"""Cheap, LLM-free gate for `POST /briefing/trigger` (api/app.py): decides
whether there's enough real signal in the currently active Service Alerts
to make an LLM call worth its cost at all.

Catches alerts that carry no `informed_entity` with a real `route_id` --
those produce nothing useful for a briefing writer to name, and this check
catches that class BEFORE spending a call, not after.

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

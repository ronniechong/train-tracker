"""Fires the weekly performance digest once when the poll loop crosses
Monday 8am Melbourne time (05-ai-layer, locked 2026-08-01 -- see
milestones/05-ai-layer.md's "Weekly performance digest" section).

Own small JSON sidecar for idempotency across poller restarts --
`gtfs/pinning.py`'s `PinManifest` is the precedent (a manifest class
owning exactly one JSON file for exactly one caller), deliberately NOT
`digests/store.py`'s SQLite content history: this is trigger idempotency
state, not digest content (see that module's own docstring).

Cold-start (locked 2026-08-01): this trigger has no opinion about how
much history exists when it fires -- it only ever answers "has the poll
loop crossed a Monday-8am boundary we haven't already fired for", the
same way on the very first Monday after deploy as on any other. The
caller (`poller/__main__.py`) is what reads however many of the 7
preceding service_dates actually exist and reports that honestly.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..gtfs.gtfstime import MELBOURNE_TZ

FIRING_HOUR_LOCAL = 8


def _most_recent_firing_monday(now: datetime, tz_name: str) -> date:
    """The Melbourne-local calendar date of the most recent Monday whose
    8am local instant is at or before `now`. A Monday before 8am itself
    resolves to the PRIOR Monday -- this week's boundary hasn't been
    reached yet."""
    local_now = now.astimezone(ZoneInfo(tz_name))
    candidate = local_now.date() - timedelta(days=local_now.weekday())  # Monday=0..Sunday=6
    candidate_8am = datetime.combine(candidate, time(FIRING_HOUR_LOCAL), tzinfo=ZoneInfo(tz_name))
    if local_now < candidate_8am:
        candidate -= timedelta(days=7)
    return candidate


class WeeklyDigestTrigger:
    def __init__(self, state_path: Path, tz_name: str = MELBOURNE_TZ):
        self._path = state_path
        self._tz_name = tz_name

    def _last_fired_monday(self) -> date | None:
        if not self._path.exists():
            return None
        raw = json.loads(self._path.read_text())
        last = raw.get("last_fired_monday")
        return date.fromisoformat(last) if last else None

    def should_fire(self, now: datetime) -> date | None:
        """Returns the boundary Monday's date if a fire is due, else
        `None`. Safe to call every poll cycle -- calling this repeatedly
        without ever calling `mark_fired` just keeps returning the same
        boundary date; it's `mark_fired` that actually advances state."""
        boundary = _most_recent_firing_monday(now, self._tz_name)
        last_fired = self._last_fired_monday()
        if last_fired is not None and last_fired >= boundary:
            return None
        return boundary

    def mark_fired(self, boundary_monday: date) -> None:
        """Must only be called AFTER the digest has actually been
        generated and delivered -- see milestones/05-ai-layer.md's crash-
        safety note. Calling this first and then failing to deliver would
        silently lose that week forever: the next check would see this
        boundary as already fired and stay quiet."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"last_fired_monday": boundary_monday.isoformat()}))

"""Refreshes precomputed Insights rollups; the aggregation job ships ahead
of any chart UI, deliberately.

Two distinct paths, deliberately not the same code path:

- `should_finalize_yesterday` / `mark_finalized`: once per newly-elapsed
  service_date, roll up the day that just closed. Reads via
  `HistoryStore.read_completion_events`, which is safe by that method's own
  existing contract (every requested date is fully elapsed by the time
  this runs) -- the same guarantee the weekly digest already relies on.
- `should_refresh_today` / `mark_refreshed`: periodically re-roll up
  TODAY's still-accumulating service_date, so "Today" in the global date
  filter (locked design decision) isn't stuck showing zero/stale data all
  day. This reads the actively-written partition -- genuinely new
  territory for this codebase; `read_completion_events`'s own docstring
  scopes itself to closed days only. A transient SQLite lock during the
  live writer's brief autocommit window is, from outside that method,
  indistinguishable from "partition file doesn't exist yet" -- both land
  in `days_missing`. `_read_today_with_retry` below disambiguates using
  `HistoryStore.partition_path().exists()` and retries a few times with a
  short backoff, specifically for today's date only -- every other caller
  of `read_completion_events` is untouched.

Idempotency state (which day was last finalized, when today was last
refreshed) uses its own small JSON sidecar, following the same pattern as
`WeeklyDigestTrigger` / `gtfs/pinning.py`'s `PinManifest` -- trigger state,
not rollup content, which is what `InsightsStore` already owns.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..gtfs.gtfstime import service_date_for_instant
from ..gtfs.routes import Route
from ..history.store import CompletionEventsWindow, HistoryStore
from ..insights.aggregate import aggregate_day
from ..insights.store import InsightsStore

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes -- frequent enough that
# "Today" doesn't look stuck, infrequent enough not to add real load on
# top of the poll loop's own 10s cadence.

_READ_TODAY_ATTEMPTS = 3
_READ_TODAY_BACKOFF_SECONDS = 0.05


def _read_today_with_retry(
    history: HistoryStore,
    today: date,
    attempts: int = _READ_TODAY_ATTEMPTS,
    backoff_seconds: float = _READ_TODAY_BACKOFF_SECONDS,
) -> CompletionEventsWindow:
    partition_exists = history.partition_path(today).exists()
    result = history.read_completion_events([today])
    if not partition_exists or today not in result.days_missing:
        return result
    for _ in range(attempts - 1):
        time.sleep(backoff_seconds)
        result = history.read_completion_events([today])
        if today not in result.days_missing:
            return result
    return result


@dataclass(frozen=True)
class _TriggerState:
    last_finalized_date: date | None
    last_refreshed_today_at: datetime | None


class InsightsTrigger:
    def __init__(
        self,
        state_path: Path,
        refresh_interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
    ):
        self._path = state_path
        self._refresh_interval_seconds = refresh_interval_seconds

    def _read_state(self) -> _TriggerState:
        if not self._path.exists():
            return _TriggerState(last_finalized_date=None, last_refreshed_today_at=None)
        raw = json.loads(self._path.read_text())
        last_finalized = raw.get("last_finalized_date")
        last_refreshed = raw.get("last_refreshed_today_at")
        return _TriggerState(
            last_finalized_date=date.fromisoformat(last_finalized) if last_finalized else None,
            last_refreshed_today_at=(
                datetime.fromisoformat(last_refreshed) if last_refreshed else None
            ),
        )

    def _write_state(self, state: _TriggerState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "last_finalized_date": (
                        state.last_finalized_date.isoformat()
                        if state.last_finalized_date
                        else None
                    ),
                    "last_refreshed_today_at": (
                        state.last_refreshed_today_at.isoformat()
                        if state.last_refreshed_today_at
                        else None
                    ),
                }
            )
        )

    def should_finalize_yesterday(self, now: datetime) -> date | None:
        """Returns yesterday's (fully-elapsed) service_date if it hasn't
        been finalized yet, else `None`. Safe to call every poll cycle."""
        yesterday = service_date_for_instant(now) - timedelta(days=1)
        state = self._read_state()
        if state.last_finalized_date is not None and state.last_finalized_date >= yesterday:
            return None
        return yesterday

    def mark_finalized(self, finalized_date: date) -> None:
        """Must only be called AFTER `InsightsStore.record_day` has
        actually succeeded for this date -- same crash-safety ordering as
        `WeeklyDigestTrigger.mark_fired`: marking first and failing to
        write would silently skip that day's rollup forever."""
        state = self._read_state()
        self._write_state(
            _TriggerState(
                last_finalized_date=finalized_date,
                last_refreshed_today_at=state.last_refreshed_today_at,
            )
        )

    def should_refresh_today(self, now: datetime) -> bool:
        state = self._read_state()
        if state.last_refreshed_today_at is None:
            return True
        elapsed = (now - state.last_refreshed_today_at).total_seconds()
        return elapsed >= self._refresh_interval_seconds

    def mark_refreshed(self, now: datetime) -> None:
        state = self._read_state()
        self._write_state(
            _TriggerState(last_finalized_date=state.last_finalized_date, last_refreshed_today_at=now)
        )


async def run_insights_cycle(
    trigger: InsightsTrigger,
    history: HistoryStore,
    insights_store: InsightsStore,
    routes: dict[str, Route],
    now: datetime,
) -> None:
    """Checked once per poll cycle -- cheap (a JSON-file read) when neither
    path is due. Never raises: an aggregation failure here must never take
    down the poll loop it runs inside, same discipline the weekly digest
    trigger already follows. Both paths log and return on failure, retried
    naturally next cycle."""
    yesterday = trigger.should_finalize_yesterday(now)
    if yesterday is not None:
        try:
            window = history.read_completion_events([yesterday])
            if yesterday in window.days_covered:
                rollup = aggregate_day(yesterday, window.events, routes)
                insights_store.record_day(rollup)
            # A closed day that's still missing (partition never opened
            # that service_date -- poller was down) is left unfinalized;
            # NOT marked, so it's retried every cycle rather than silently
            # skipped -- matches read_completion_events's own
            # days_covered/days_missing honesty distinction.
            if yesterday in window.days_covered:
                trigger.mark_finalized(yesterday)
        except Exception:
            logger.exception("insights finalize-yesterday failed, will retry next cycle")

    if trigger.should_refresh_today(now):
        try:
            today = service_date_for_instant(now)
            window = _read_today_with_retry(history, today)
            if today in window.days_covered:
                rollup = aggregate_day(today, window.events, routes)
                insights_store.record_day(rollup)
            trigger.mark_refreshed(now)
        except Exception:
            logger.exception("insights refresh-today failed, will retry next cycle")

"""SQLite-backed monthly budget cap for AI-layer LLM calls (M5 kickoff
decision, 2026-07-31): checked before every call, incremented after --
matches this project's existing SQLite-for-local-state pattern (see
history/store.py) rather than polling Anthropic's usage API, which would
add external latency to a check that has to happen on every call.

One row per calendar month (UTC), not day-partitioned like history/
store.py -- the cap window here is a month, not a service day, so there's
no reason to open/close a new file per day the way history's retention
model requires.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .llm_client import (
    HAIKU_INPUT_USD_PER_MTOK,
    HAIKU_OUTPUT_USD_PER_MTOK,
    LLMClient,
    LLMResponse,
    estimate_cost_usd,
)

# First-cut value, not tuned against real usage -- revisit once 05e/05f
# produce real traffic, matching this project's "first-cut constants,
# revisit at soak gate" convention (e.g. GEOFENCE_RADIUS_M,
# COASTING_TIMEOUT_S in CLAUDE.md's settled-decisions table).
DEFAULT_MONTHLY_BUDGET_USD = 20.0

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS monthly_spend (
        month TEXT PRIMARY KEY,
        cost_usd REAL NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        call_count INTEGER NOT NULL DEFAULT 0
    )
"""


class BudgetExceededError(Exception):
    pass


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


class BudgetTracker:
    def __init__(self, db_path: Path, monthly_cap_usd: float = DEFAULT_MONTHLY_BUDGET_USD):
        self._monthly_cap_usd = monthly_cap_usd
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit, same reasoning as history/store.py: write volume is
        # one row-update per LLM call (never per poll cycle), so
        # per-statement commit overhead is a non-issue, and a crash right
        # after a `record()` never loses an uncommitted spend update.
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute(_CREATE_TABLE_SQL)

    def spent_usd(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT cost_usd FROM monthly_spend WHERE month = ?", (_month_key(now),)
        ).fetchone()
        return row[0] if row else 0.0

    def check(self, now: datetime | None = None) -> None:
        """Raises if this calendar month has already hit the cap. This
        can't know a not-yet-made call's own cost, so the cap is enforced
        as "don't start a new call once already at or over budget," not
        "never exceed it by even one call's worth" -- acceptable given
        Haiku's real per-call cost is tiny relative to the cap (~$0.003-
        0.004/briefing per the M5 kickoff estimate)."""
        now = now or datetime.now(timezone.utc)
        if self.spent_usd(now) >= self._monthly_cap_usd:
            raise BudgetExceededError(
                f"monthly AI budget (${self._monthly_cap_usd:.2f}) reached for {_month_key(now)}"
            )

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        cost = estimate_cost_usd(input_tokens, output_tokens, input_usd_per_mtok, output_usd_per_mtok)
        self._conn.execute(
            """
            INSERT INTO monthly_spend (month, cost_usd, input_tokens, output_tokens, call_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(month) DO UPDATE SET
                cost_usd = cost_usd + excluded.cost_usd,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                call_count = call_count + 1
            """,
            (_month_key(now), cost, input_tokens, output_tokens),
        )

    def close(self) -> None:
        self._conn.close()


class BudgetEnforcedLLMClient:
    """Wraps any `LLMClient` with the budget check/record pair -- the
    same composable-wrapper shape `metrics.event_logs()` already uses to
    wrap TU/VP discrepancy/ghost/gap `EventLog`s (state/store.py), not a
    pattern invented specifically for the AI layer."""

    def __init__(
        self,
        inner: LLMClient,
        tracker: BudgetTracker,
        input_usd_per_mtok: float = HAIKU_INPUT_USD_PER_MTOK,
        output_usd_per_mtok: float = HAIKU_OUTPUT_USD_PER_MTOK,
    ):
        self._inner = inner
        self._tracker = tracker
        self._input_usd_per_mtok = input_usd_per_mtok
        self._output_usd_per_mtok = output_usd_per_mtok

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int,
    ) -> LLMResponse:
        self._tracker.check()
        response = await self._inner.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        self._tracker.record(
            response.input_tokens,
            response.output_tokens,
            self._input_usd_per_mtok,
            self._output_usd_per_mtok,
        )
        return response

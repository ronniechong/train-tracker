from datetime import datetime, timezone

import pytest

from traintracker.ai.budget import BudgetEnforcedLLMClient, BudgetExceededError, BudgetTracker
from traintracker.ai.llm_client import LLMResponse

JULY = datetime(2026, 7, 31, tzinfo=timezone.utc)
AUGUST = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_check_passes_when_nothing_spent(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=1.0)
    tracker.check(JULY)  # no exception


def test_record_accumulates_cost_and_check_raises_once_over_cap(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=0.5)

    tracker.record(500_000, 0, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0, now=JULY)  # $0.50
    assert tracker.spent_usd(JULY) == pytest.approx(0.5)

    with pytest.raises(BudgetExceededError):
        tracker.check(JULY)


def test_record_combines_input_and_output_cost(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=10.0)

    # 1M input tokens @ $1/MTok + 200K output tokens @ $5/MTok = $1.00 + $1.00
    tracker.record(1_000_000, 200_000, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0, now=JULY)

    assert tracker.spent_usd(JULY) == pytest.approx(2.0)


def test_record_calls_accumulate_within_the_same_month(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=10.0)

    for _ in range(3):
        tracker.record(100_000, 0, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0, now=JULY)

    assert tracker.spent_usd(JULY) == pytest.approx(0.3)


def test_spend_resets_across_calendar_months(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=0.5)

    tracker.record(1_000_000, 0, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0, now=JULY)  # $1, over July's cap
    with pytest.raises(BudgetExceededError):
        tracker.check(JULY)

    tracker.check(AUGUST)  # August starts fresh, no exception


class _FakeInnerClient:
    def __init__(self, response: LLMResponse):
        self._response = response
        self.calls = 0

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls += 1
        return self._response


def _response(input_tokens=1000, output_tokens=500) -> LLMResponse:
    return LLMResponse(
        text="hi", tool_uses=(), stop_reason="end_turn",
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


async def test_budget_enforced_client_records_spend_after_a_successful_call(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=10.0)
    inner = _FakeInnerClient(_response(input_tokens=1_000_000, output_tokens=200_000))
    wrapped = BudgetEnforcedLLMClient(inner, tracker, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0)

    result = await wrapped.complete(system="s", messages=[], max_tokens=10)

    assert result.text == "hi"
    assert inner.calls == 1
    assert tracker.spent_usd() == pytest.approx(2.0)


async def test_budget_enforced_client_blocks_call_when_already_over_cap(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.db", monthly_cap_usd=1.0)
    tracker.record(1_000_000, 0, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0)  # $1, at the cap
    inner = _FakeInnerClient(_response())
    wrapped = BudgetEnforcedLLMClient(inner, tracker, input_usd_per_mtok=1.0, output_usd_per_mtok=5.0)

    with pytest.raises(BudgetExceededError):
        await wrapped.complete(system="s", messages=[], max_tokens=10)

    # The check happens BEFORE delegating -- a blocked call must not reach
    # the inner client at all, or the budget cap would be advisory only.
    assert inner.calls == 0

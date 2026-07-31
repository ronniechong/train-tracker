from traintracker.ai.briefing import compose_briefing
from traintracker.ai.briefing_trigger import TriggerReason
from traintracker.ai.llm_client import LLMResponse
from traintracker.ai.tools import ToolContext
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore


class _ScriptedLLMClient:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "max_tokens": max_tokens})
        return self._responses.pop(0)


def _tool_context() -> ToolContext:
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    return ToolContext(store=store, schedule_cache=None)


async def test_compose_briefing_returns_the_final_text():
    client = _ScriptedLLMClient(
        [LLMResponse(
            text="Belgrave line: buses replace trains due to track works.",
            tool_uses=(), stop_reason="end_turn", input_tokens=50, output_tokens=20,
        )]
    )
    reason = TriggerReason("new_alert", "Buses replace trains between X and Y")

    text = await compose_briefing(client, _tool_context(), reason)

    assert text == "Belgrave line: buses replace trains due to track works."


async def test_compose_briefing_passes_the_trigger_reason_into_the_user_message():
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )
    reason = TriggerReason("cancellation_threshold", "5 cancellations in the last 15 min")

    await compose_briefing(client, _tool_context(), reason)

    user_message = client.calls[0]["messages"][0]["content"]
    assert "cancellation_threshold" in user_message
    assert "5 cancellations in the last 15 min" in user_message

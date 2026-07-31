import pytest

from traintracker.ai.agent import MaxToolIterationsExceeded, run_agent
from traintracker.ai.llm_client import LLMResponse, ToolUseBlock


class _ScriptedLLMClient:
    """Returns one scripted `LLMResponse` per call, in order -- lets a
    test drive the loop through a specific tool_use -> end_turn sequence
    without a real (or even fake-realistic) Anthropic client."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "max_tokens": max_tokens})
        return self._responses.pop(0)


async def _echo_tool(ctx, **kwargs):
    return {"received": kwargs}


async def test_run_agent_returns_text_when_no_tool_use():
    client = _ScriptedLLMClient(
        [LLMResponse(text="hello", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    result = await run_agent(
        client, system="sys", user_message="hi", tools=[], tool_functions={}, tool_context=None
    )

    assert result.text == "hello"
    assert result.tool_calls == 0
    assert len(client.calls) == 1


async def test_run_agent_executes_a_tool_and_feeds_result_back():
    client = _ScriptedLLMClient(
        [
            LLMResponse(
                text="",
                tool_uses=(ToolUseBlock(id="tu_1", name="echo", input={"x": 1}),),
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=5,
            ),
            LLMResponse(text="done", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1),
        ]
    )

    result = await run_agent(
        client,
        system="sys",
        user_message="hi",
        tools=[{"name": "echo"}],
        tool_functions={"echo": _echo_tool},
        tool_context=None,
    )

    assert result.text == "done"
    assert result.tool_calls == 1
    assert len(client.calls) == 2

    second_call_messages = client.calls[1]["messages"]
    assert second_call_messages[0] == {"role": "user", "content": "hi"}
    assert second_call_messages[1]["role"] == "assistant"
    assert second_call_messages[1]["content"][0]["type"] == "tool_use"
    tool_result_message = second_call_messages[2]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["tool_use_id"] == "tu_1"
    assert '"x": 1' in tool_result_message["content"][0]["content"]


async def test_run_agent_reports_unknown_tool_without_crashing():
    client = _ScriptedLLMClient(
        [
            LLMResponse(
                text="",
                tool_uses=(ToolUseBlock(id="tu_1", name="not_a_real_tool", input={}),),
                stop_reason="tool_use",
                input_tokens=1,
                output_tokens=1,
            ),
            LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1),
        ]
    )

    result = await run_agent(
        client, system="sys", user_message="hi", tools=[], tool_functions={}, tool_context=None
    )

    assert result.text == "ok"
    tool_result_content = client.calls[1]["messages"][2]["content"][0]["content"]
    assert "unknown tool" in tool_result_content


async def test_run_agent_raises_when_stuck_in_a_tool_loop():
    always_tool_use = LLMResponse(
        text="",
        tool_uses=(ToolUseBlock(id="tu_1", name="echo", input={}),),
        stop_reason="tool_use",
        input_tokens=1,
        output_tokens=1,
    )
    client = _ScriptedLLMClient([always_tool_use] * 3)

    with pytest.raises(MaxToolIterationsExceeded):
        await run_agent(
            client,
            system="sys",
            user_message="hi",
            tools=[],
            tool_functions={"echo": _echo_tool},
            tool_context=None,
            max_iterations=3,
        )

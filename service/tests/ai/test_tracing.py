import pytest

from traintracker.ai.llm_client import HAIKU_MODEL, LLMResponse, ToolUseBlock
from traintracker.ai.tracing import LangfuseTracedLLMClient


class _FakeGeneration:
    def __init__(self):
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _FakeObservationContext:
    def __init__(self, generation: _FakeGeneration):
        self._generation = generation

    def __enter__(self):
        return self._generation

    def __exit__(self, exc_type, exc, tb):
        return False  # never suppress -- exceptions must still propagate


class _FakeLangfuse:
    def __init__(self):
        self.calls: list[dict] = []
        self.generation = _FakeGeneration()

    def start_as_current_observation(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeObservationContext(self.generation)


class _FakeInnerClient:
    def __init__(self, response=None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls = 0

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._response


def _response(text="hi", tool_uses=(), input_tokens=1_000_000, output_tokens=200_000) -> LLMResponse:
    return LLMResponse(
        text=text, tool_uses=tool_uses, stop_reason="end_turn",
        input_tokens=input_tokens, output_tokens=output_tokens,
    )


async def test_records_a_generation_span_for_a_successful_call():
    fake_langfuse = _FakeLangfuse()
    inner = _FakeInnerClient(response=_response())
    client = LangfuseTracedLLMClient(
        inner, langfuse_client=fake_langfuse, name="test-call",
        input_usd_per_mtok=1.0, output_usd_per_mtok=5.0,
    )

    result = await client.complete(
        system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100
    )

    assert result.text == "hi"
    assert inner.calls == 1

    start_call = fake_langfuse.calls[0]
    assert start_call["name"] == "test-call"
    assert start_call["as_type"] == "generation"
    assert start_call["model"] == HAIKU_MODEL
    assert start_call["input"] == {"system": "sys", "messages": [{"role": "user", "content": "hi"}]}
    assert start_call["model_parameters"] == {"max_tokens": 100}

    update = fake_langfuse.generation.updates[0]
    assert update["output"] == "hi"
    assert update["metadata"] == {"stop_reason": "end_turn"}
    assert update["usage_details"] == {"input": 1_000_000, "output": 200_000}
    # 1M input tokens @ $1/MTok + 200K output tokens @ $5/MTok = $1.00 + $1.00
    assert update["cost_details"] == pytest.approx({"input": 1.0, "output": 1.0, "total": 2.0})


async def test_records_tool_use_blocks_as_output_when_there_is_no_text():
    fake_langfuse = _FakeLangfuse()
    tool_use = ToolUseBlock(id="tu_1", name="get_trip", input={"trip_id": "T1"})
    inner = _FakeInnerClient(response=_response(text="", tool_uses=(tool_use,)))
    client = LangfuseTracedLLMClient(inner, langfuse_client=fake_langfuse)

    await client.complete(system="sys", messages=[], max_tokens=100)

    update = fake_langfuse.generation.updates[0]
    assert update["output"] == [{"tool": "get_trip", "input": {"trip_id": "T1"}}]


async def test_inner_exception_marks_the_span_as_an_error_and_still_propagates():
    fake_langfuse = _FakeLangfuse()
    inner = _FakeInnerClient(exc=RuntimeError("boom"))
    client = LangfuseTracedLLMClient(inner, langfuse_client=fake_langfuse)

    with pytest.raises(RuntimeError, match="boom"):
        await client.complete(system="sys", messages=[], max_tokens=100)

    update = fake_langfuse.generation.updates[0]
    assert update == {"level": "ERROR", "status_message": "boom"}

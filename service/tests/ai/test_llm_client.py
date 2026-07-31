from traintracker.ai.llm_client import HAIKU_MODEL, AnthropicLLMClient


class _FakeBlock:
    def __init__(self, type, **kwargs):
        self.type = type
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


async def test_complete_parses_text_and_usage():
    response = _FakeResponse(
        content=[_FakeBlock("text", text="hello")],
        stop_reason="end_turn",
        usage=_FakeUsage(input_tokens=10, output_tokens=5),
    )
    fake_client = _FakeAnthropicClient(response)
    client = AnthropicLLMClient(client=fake_client)

    result = await client.complete(
        system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100
    )

    assert result.text == "hello"
    assert result.tool_uses == ()
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 10
    assert result.output_tokens == 5

    call = fake_client.messages.calls[0]
    assert call["model"] == HAIKU_MODEL
    assert call["max_tokens"] == 100
    assert "tools" not in call  # no tools passed -> omitted, not sent as []


async def test_complete_parses_tool_use_blocks():
    response = _FakeResponse(
        content=[
            _FakeBlock("text", text="checking..."),
            _FakeBlock("tool_use", id="tu_1", name="get_trip", input={"trip_id": "T1"}),
        ],
        stop_reason="tool_use",
        usage=_FakeUsage(input_tokens=20, output_tokens=8),
    )
    fake_client = _FakeAnthropicClient(response)
    client = AnthropicLLMClient(client=fake_client)
    tools = [{"name": "get_trip", "description": "...", "input_schema": {}}]

    result = await client.complete(
        system="sys",
        messages=[{"role": "user", "content": "where's my train"}],
        tools=tools,
        max_tokens=100,
    )

    assert result.text == "checking..."
    assert len(result.tool_uses) == 1
    assert result.tool_uses[0].id == "tu_1"
    assert result.tool_uses[0].name == "get_trip"
    assert result.tool_uses[0].input == {"trip_id": "T1"}
    assert result.stop_reason == "tool_use"
    assert fake_client.messages.calls[0]["tools"] == tools


async def test_complete_defaults_to_haiku_model():
    response = _FakeResponse(
        content=[], stop_reason="end_turn", usage=_FakeUsage(input_tokens=1, output_tokens=1)
    )
    fake_client = _FakeAnthropicClient(response)
    client = AnthropicLLMClient(client=fake_client)

    await client.complete(system="sys", messages=[], max_tokens=10)

    assert fake_client.messages.calls[0]["model"] == "claude-haiku-4-5"

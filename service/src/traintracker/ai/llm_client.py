"""Thin interface around whichever LLM SDK client this project uses --
M5 kickoff's swappable-provider decision (2026-07-31): Anthropic Haiku
today, built behind one call-site method wrapping the SDK call so a later
provider swap (Groq/OpenRouter, if cost ever demands it) is a new adapter
implementing this same interface, not a rewrite of every caller.

Every AI-layer caller (05b's tool-calling loop, 05e's briefings, 05f's NL
query) routes through `LLMClient.complete()` -- Langfuse tracing and the
budget cap (`ai/budget.py`) both wrap an `LLMClient` instance, not the raw
Anthropic SDK client, so neither concern needs to know Anthropic specifics.

Tool-calling implementation is Anthropic SDK directly (kickoff decision:
LangChain considered and rejected -- 3 read-only local tools with no
memory/chaining don't need `AgentExecutor`'s machinery, and the provider-
swap benefit is already covered by this interface). `AnthropicLLMClient`
IS that adapter boundary: everything Anthropic-specific lives here, same
"keep all gateway specifics in ONE client module" pattern
`gateway/client.py` already follows for the upstream GTFS-R feeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic

ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
HAIKU_MODEL = "claude-haiku-4-5"

# Haiku 4.5 pricing (claude-api skill, cached 2026-06-24): $1.00 / $5.00
# per MTok input/output. Used by ai/budget.py to convert a response's
# token usage into an estimated USD cost -- this module only carries the
# constants, since a provider swap would replace both the model id and
# its pricing together.
HAIKU_INPUT_USD_PER_MTOK = 1.00
HAIKU_OUTPUT_USD_PER_MTOK = 5.00


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    input_usd_per_mtok: float,
    output_usd_per_mtok: float,
) -> float:
    """Shared by `ai/budget.py` (spend tracking) and `ai/tracing.py`
    (Langfuse cost_details) -- both need the same token-count -> USD
    conversion, so it lives once next to the pricing constants rather
    than being duplicated in each wrapper."""
    return (input_tokens / 1_000_000) * input_usd_per_mtok + (
        output_tokens / 1_000_000
    ) * output_usd_per_mtok


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMResponse:
    text: str  # concatenated text blocks; "" for a pure tool_use turn
    tool_uses: tuple[ToolUseBlock, ...]
    stop_reason: str | None  # "end_turn" | "tool_use" | "max_tokens" | ...
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int,
    ) -> LLMResponse: ...


class AnthropicLLMClient:
    """The only implementation today. `client` is injectable purely for
    testing (mirrors `GatewayClient`'s own test seam) -- production
    callers should leave it `None` and let the SDK resolve credentials
    from the environment (`ANTHROPIC_API_KEY`), not construct a key
    in-process."""

    def __init__(self, client: anthropic.AsyncAnthropic | None = None, model: str = HAIKU_MODEL):
        self._client = client or anthropic.AsyncAnthropic()
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int,
    ) -> LLMResponse:
        kwargs = dict(model=self._model, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        response = await self._client.messages.create(**kwargs)

        text = "".join(block.text for block in response.content if block.type == "text")
        tool_uses = tuple(
            ToolUseBlock(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if block.type == "tool_use"
        )
        return LLMResponse(
            text=text,
            tool_uses=tool_uses,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

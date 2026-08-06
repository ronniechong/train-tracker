"""Langfuse tracing wrapper for `LLMClient`.

Wired once at the single `LLMClient` interface every AI-layer caller
routes through -- every inference should be reconstructable (prompt
version, inputs, cost), and this is the one instrumentation point that
covers all call paths without separate wiring per caller.

Uses `start_as_current_observation(as_type="generation")` rather than a
bespoke root-trace helper: the Langfuse SDK's OTEL context propagation
means a call made while another span is already current nests under it
automatically -- this module never needs to know whether it's the only
LLM call in a request or one of several tool-calling round-trips inside a
single agent turn.
"""

from __future__ import annotations

from typing import Any

from langfuse import Langfuse

from .llm_client import (
    HAIKU_INPUT_USD_PER_MTOK,
    HAIKU_MODEL,
    HAIKU_OUTPUT_USD_PER_MTOK,
    LLMClient,
    LLMResponse,
    estimate_cost_usd,
)


class LangfuseTracedLLMClient:
    """Wraps any `LLMClient` with a Langfuse generation span per call --
    same composable-wrapper shape `BudgetEnforcedLLMClient` (ai/budget.py)
    already uses, not a pattern invented specifically for tracing.

    `langfuse_client` is injectable purely for testing (mirrors
    `AnthropicLLMClient`'s own test seam) -- production callers should
    leave it `None` and let the SDK resolve `LANGFUSE_PUBLIC_KEY`/
    `LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` from the environment.

    `name` identifies the call site in the Langfuse dashboard (e.g.
    "agent-tool-loop", "disruption-briefing") -- a caller constructs its
    own instance with its own name rather than this module threading a
    per-call name through the shared `LLMClient.complete()` signature,
    which every other wrapper and `run_agent()` also depend on staying
    fixed."""

    def __init__(
        self,
        inner: LLMClient,
        langfuse_client: Langfuse | None = None,
        name: str = "llm-complete",
        model: str = HAIKU_MODEL,
        input_usd_per_mtok: float = HAIKU_INPUT_USD_PER_MTOK,
        output_usd_per_mtok: float = HAIKU_OUTPUT_USD_PER_MTOK,
    ):
        self._inner = inner
        self._langfuse = langfuse_client or Langfuse()
        self._name = name
        self._model = model
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
        with self._langfuse.start_as_current_observation(
            name=self._name,
            as_type="generation",
            model=self._model,
            input={"system": system, "messages": messages},
            model_parameters={"max_tokens": max_tokens},
        ) as generation:
            try:
                response = await self._inner.complete(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens
                )
            except Exception as exc:
                # Real bug or upstream failure, not an expected tool
                # outcome (those are `{"error": ...}` payloads handled in
                # ai/agent.py, never exceptions) -- record it on the trace
                # then let it propagate.
                generation.update(level="ERROR", status_message=str(exc))
                raise

            output: Any = response.text or [
                {"tool": block.name, "input": block.input} for block in response.tool_uses
            ]
            cost = estimate_cost_usd(
                response.input_tokens,
                response.output_tokens,
                self._input_usd_per_mtok,
                self._output_usd_per_mtok,
            )
            generation.update(
                output=output,
                metadata={"stop_reason": response.stop_reason},
                usage_details={"input": response.input_tokens, "output": response.output_tokens},
                cost_details={
                    "input": (response.input_tokens / 1_000_000) * self._input_usd_per_mtok,
                    "output": (response.output_tokens / 1_000_000) * self._output_usd_per_mtok,
                    "total": cost,
                },
            )
            return response

"""Manual Anthropic tool-calling loop, chosen over a framework or the beta
Tool Runner -- a few read-only local tools with no memory/chaining don't
need that machinery.

Generic over whatever tool registry a caller supplies -- this module has
no opinion about what the tools DO, only about driving the
request/tool_use/tool_result cycle to completion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .llm_client import LLMClient, LLMResponse, ToolUseBlock

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


class MaxToolIterationsExceeded(Exception):
    pass


@dataclass(frozen=True)
class AgentResult:
    text: str
    tool_calls: int


def _assistant_content(response: LLMResponse) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if response.text:
        content.append({"type": "text", "text": response.text})
    for block in response.tool_uses:
        content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    return content


async def _execute_tool(
    tool_functions: dict[str, ToolFunction], tool_context: Any, block: ToolUseBlock
) -> dict[str, Any]:
    fn = tool_functions.get(block.name)
    if fn is None:
        return {"error": f"unknown tool {block.name!r}"}
    return await fn(tool_context, **block.input)


async def run_agent(
    client: LLMClient,
    *,
    system: str,
    user_message: str,
    tools: list[dict[str, Any]],
    tool_functions: dict[str, ToolFunction],
    tool_context: Any,
    max_tokens: int = 1024,
    max_iterations: int = 5,
) -> AgentResult:
    """Drives one user turn to completion: call the model, execute any
    tool_use blocks locally (every tool reads only local state passed in
    via `tool_context`, never a fresh upstream request), feed results
    back, repeat until `end_turn` or `max_iterations`.

    A tool function raising is NOT caught here -- expected failure modes
    (unknown line, untracked trip) are `{"error": ...}` return values the
    model can read and react to; an actual exception means a real bug and
    should propagate rather than be absorbed into a turn that looks like
    it succeeded."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    tool_calls = 0

    for _ in range(max_iterations):
        response = await client.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        if response.stop_reason != "tool_use":
            return AgentResult(text=response.text, tool_calls=tool_calls)

        messages.append({"role": "assistant", "content": _assistant_content(response)})
        tool_results = []
        for block in response.tool_uses:
            tool_calls += 1
            result = await _execute_tool(tool_functions, tool_context, block)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
            )
        messages.append({"role": "user", "content": tool_results})

    raise MaxToolIterationsExceeded(
        f"agent turn did not finish within {max_iterations} tool-calling iterations"
    )

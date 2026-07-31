"""One-off eval harness for 05e's on-demand briefing composer -- NOT part
of the automated pytest suite (makes real, paid Haiku calls; needs
ANTHROPIC_API_KEY + a real pinned static snapshot on disk). Run this
before shipping any change to `ai/briefing.py`'s system prompt or
`ai/briefing_filter.py`'s gate, to catch a regression the milestone's
original scope explicitly named: "small eval set: known network states
-> expected briefing content, scored on every prompt change".

Deliberately NOT LLM-as-judge -- every check here is a simple,
deterministic keyword/length assertion, keeping this fast, cheap, and
free of a second model's own nondeterminism. A case that should be
filtered before ever reaching the LLM (ai/briefing_filter.py) costs
nothing to run; only the genuinely briefable cases make a real call.

Run: `uv run python scripts/eval_briefings.py --gtfs-dir <dir> --digest <digest>`
from `service/`, with ANTHROPIC_API_KEY set (e.g.
`set -a && source ../deploy/.env && set +a`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from traintracker.ai.briefing import compose_briefing  # noqa: E402
from traintracker.ai.briefing_filter import has_briefable_alerts  # noqa: E402
from traintracker.ai.llm_client import AnthropicLLMClient  # noqa: E402
from traintracker.ai.tools import ToolContext  # noqa: E402
from traintracker.gtfs.pinning import PinManifest  # noqa: E402
from traintracker.gtfs.schedule_cache import PinnedScheduleCache  # noqa: E402
from traintracker.state.alerts import Alert, InformedEntity  # noqa: E402
from traintracker.state.eventlog import InMemoryEventLog  # noqa: E402
from traintracker.state.store import StateStore  # noqa: E402

NOW = datetime.now(timezone.utc)


@dataclass
class EvalCase:
    name: str
    store: StateStore
    expect_briefable: bool
    # Case-insensitive substrings -- ALL must appear in the composed text.
    # Only checked when expect_briefable is True (nothing to score otherwise).
    must_contain: list[str] = field(default_factory=list)
    max_words: int = 90


def _alert(alert_id: str, header_text: str, route_id: str | None, effect: str = "SIGNIFICANT_DELAYS") -> Alert:
    return Alert(
        id=alert_id, cause="MAINTENANCE", effect=effect, header_text=header_text,
        description_text=None, url=None, active_periods=(),
        informed_entities=() if route_id is None else (
            InformedEntity(route_id=route_id, stop_id=None, direction_id=None),
        ),
    )


def _store(*alerts: Alert) -> StateStore:
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    store.latest_alerts = {a.id: a for a in alerts}
    return store


def _cases() -> list[EvalCase]:
    return [
        EvalCase(
            name="single_clear_alert_belgrave",
            store=_store(_alert("A1", "Buses replace trains between Bayswater and Belgrave", "2-BEG")),
            expect_briefable=True,
            must_contain=["belgrave"],
        ),
        EvalCase(
            name="multiple_alerts_two_lines",
            store=_store(
                _alert("A1", "Buses replace trains between Bayswater and Belgrave", "2-BEG"),
                _alert("A2", "Trains will not stop at Boronia due to planned works", "2-BEG"),
                _alert("A3", "Reduced service on the Craigieburn line", "2-CGB"),
            ),
            expect_briefable=True,
            # At least one real line name should surface -- checked via OR
            # semantics below, not a strict ALL-must-match like other cases.
            must_contain=["belgrave", "craigieburn"],
        ),
        EvalCase(
            name="vague_alert_no_route_data_never_reaches_llm",
            # The exact real-world failure mode this project hit
            # (2026-08-01): "Major Delay... cannot determine which
            # line(s)". Must be filtered before any LLM call, not just
            # produce a hedged response after paying for one.
            store=_store(_alert("A1", "Major Delay", route_id=None)),
            expect_briefable=False,
        ),
        EvalCase(
            name="no_active_alerts_never_reaches_llm",
            store=_store(),
            expect_briefable=False,
        ),
    ]


def _score(case: EvalCase, text: str) -> list[str]:
    failures = []
    word_count = len(text.split())
    if word_count > case.max_words:
        failures.append(f"too long: {word_count} words (max {case.max_words})")
    lowered = text.lower()
    if case.must_contain and not any(kw in lowered for kw in case.must_contain):
        failures.append(f"missing all of {case.must_contain!r} in output: {text!r}")
    return failures


async def _run_case(case: EvalCase, client: AnthropicLLMClient, tool_context: ToolContext) -> tuple[bool, str]:
    briefable = has_briefable_alerts(case.store, NOW)
    if briefable != case.expect_briefable:
        return False, f"filter mismatch: expected briefable={case.expect_briefable}, got {briefable}"
    if not case.expect_briefable:
        return True, "correctly filtered, no LLM call made"

    text = await compose_briefing(client, tool_context)
    failures = _score(case, text)
    if failures:
        return False, "; ".join(failures)
    return True, f"ok: {text!r}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtfs-dir", required=True, help="directory containing <digest>.zip")
    parser.add_argument("--digest", required=True, help="pinned static snapshot digest")
    args = parser.parse_args()

    gtfs_dir = Path(args.gtfs_dir)
    manifest_path = gtfs_dir / "eval_pin_manifest.json"
    manifest = PinManifest(manifest_path)
    manifest.pin_digest(date.today(), args.digest)
    schedule_cache = PinnedScheduleCache(gtfs_dir, manifest)

    client = AnthropicLLMClient()
    all_passed = True
    for case in _cases():
        tool_context = ToolContext(store=case.store, schedule_cache=schedule_cache)
        passed, detail = await _run_case(case, client, tool_context)
        all_passed = all_passed and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {case.name}: {detail}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

from datetime import date, datetime, timezone

from traintracker.ai.llm_client import LLMResponse
from traintracker.ai.weekly_digest import (
    LineStats,
    WeeklyStats,
    aggregate_weekly_stats,
    compose_weekly_digest,
)
from traintracker.gtfs.routes import Route
from traintracker.history.store import CompletionEventsWindow
from traintracker.state.completion import TripCompletionEvent

WEEK_START = date(2026, 7, 27)
WEEK_END = date(2026, 8, 2)


_SCHEDULED_ARRIVAL = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)


def _event(
    trip_id="t1", route_id="2-BEG", status="on_time", delay_seconds=0,
) -> TripCompletionEvent:
    return TripCompletionEvent(
        trip_id=trip_id, route_id=route_id, service_date="2026-07-27",
        scheduled_terminus_arrival=_SCHEDULED_ARRIVAL,
        actual_terminus_arrival=_SCHEDULED_ARRIVAL if status in ("on_time", "late") else None,
        delay_seconds=delay_seconds if status in ("on_time", "late") else None,
        status=status,
    )


def _window(events, days_covered=7, days_missing=0) -> CompletionEventsWindow:
    covered = tuple(date(2026, 7, 27 + i) if i < 5 else date(2026, 8, i - 4) for i in range(days_covered))
    missing = tuple(date(2026, 8, 10 + i) for i in range(days_missing))
    return CompletionEventsWindow(events=tuple(events), days_covered=covered, days_missing=missing)


def test_basic_counts_and_on_time_pct():
    events = [
        _event("t1", status="on_time"),
        _event("t2", status="on_time"),
        _event("t3", status="on_time"),
        _event("t4", status="late"),
        _event("t5", status="cancelled"),
    ]
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END)

    assert stats.on_time_count == 3
    assert stats.late_count == 1
    assert stats.cancelled_count == 1
    # on_time_pct is of trips that RAN (3 + 1 = 4), cancelled excluded from the denominator.
    assert stats.on_time_pct == 75.0
    assert stats.week_start == WEEK_START
    assert stats.week_end == WEEK_END


def test_undetermined_gap_events_are_excluded_entirely():
    # Locked decision (milestone doc, tracking-layer scoping pass): no
    # visible "N undetermined" count anywhere in a consuming digest.
    events = [
        _event("t1", status="on_time"),
        _event("t2", status="undetermined_gap"),
        _event("t3", status="undetermined_gap"),
    ]
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END)

    assert stats.on_time_count == 1
    assert stats.late_count == 0
    assert stats.cancelled_count == 0
    assert stats.on_time_pct == 100.0
    assert stats.line_stats == () or all(
        line.on_time_count + line.late_count + line.cancelled_count == 1 for line in stats.line_stats
    )


def test_on_time_pct_is_zero_not_a_crash_when_no_trips_ran():
    events = [_event("t1", status="cancelled"), _event("t2", status="undetermined_gap")]
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END)

    assert stats.on_time_count == 0
    assert stats.late_count == 0
    assert stats.on_time_pct == 0.0


def test_days_covered_reflects_the_window_not_a_hardcoded_seven():
    events = [_event("t1", status="on_time")]
    stats = aggregate_weekly_stats(_window(events, days_covered=5), WEEK_START, WEEK_END)

    assert stats.days_covered == 5


def test_per_line_ranking_applies_the_minimum_sample_size_floor():
    # 2-BEG: 25 trips that ran (above the 20-trip floor) -> appears.
    # 2-CRB: 10 trips that ran (below the floor) -> excluded, even though
    # its raw on_time_pct would otherwise look meaningful.
    events = (
        [_event(f"beg{i}", route_id="2-BEG", status="on_time") for i in range(20)]
        + [_event(f"beg-late{i}", route_id="2-BEG", status="late") for i in range(5)]
        + [_event(f"crb{i}", route_id="2-CRB", status="on_time") for i in range(10)]
    )
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END, min_sample_size=20)

    route_ids = {line.route_id for line in stats.line_stats}
    assert route_ids == {"2-BEG"}
    beg = next(line for line in stats.line_stats if line.route_id == "2-BEG")
    assert beg.trip_count == 25
    assert beg.on_time_count == 20
    assert beg.late_count == 5
    assert beg.on_time_pct == 80.0


def test_cancelled_trips_excluded_from_per_line_trip_count_and_pct():
    # A line's cancelled trips are tracked (cancelled_count) but must not
    # inflate trip_count or distort on_time_pct's denominator -- same
    # reliability/punctuality split as the network-wide totals.
    events = (
        [_event(f"beg{i}", route_id="2-BEG", status="on_time") for i in range(20)]
        + [_event(f"beg-cancel{i}", route_id="2-BEG", status="cancelled") for i in range(5)]
    )
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END, min_sample_size=20)

    [beg] = stats.line_stats
    assert beg.trip_count == 20  # cancelled NOT included
    assert beg.cancelled_count == 5
    assert beg.on_time_pct == 100.0


def test_events_with_no_route_id_count_toward_totals_but_not_any_line():
    events = [
        _event("t1", route_id=None, status="on_time"),
        *[_event(f"beg{i}", route_id="2-BEG", status="on_time") for i in range(20)],
    ]
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END, min_sample_size=20)

    assert stats.on_time_count == 21  # network total includes the route_id=None trip
    [beg] = stats.line_stats
    assert beg.on_time_count == 20  # but it's not attributed to any line


def test_line_stats_sorted_best_on_time_pct_first():
    events = (
        [_event(f"beg{i}", route_id="2-BEG", status="on_time") for i in range(15)]
        + [_event(f"beg-late{i}", route_id="2-BEG", status="late") for i in range(5)]  # 75%
        + [_event(f"crb{i}", route_id="2-CRB", status="on_time") for i in range(20)]  # 100%
        + [_event(f"pkm{i}", route_id="2-PKM", status="on_time") for i in range(10)]
        + [_event(f"pkm-late{i}", route_id="2-PKM", status="late") for i in range(10)]  # 50%
    )
    stats = aggregate_weekly_stats(_window(events), WEEK_START, WEEK_END, min_sample_size=20)

    assert [line.route_id for line in stats.line_stats] == ["2-CRB", "2-BEG", "2-PKM"]


class _ScriptedLLMClient:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "max_tokens": max_tokens})
        return self._responses.pop(0)


def _stats(line_stats=()) -> WeeklyStats:
    return WeeklyStats(
        week_start=WEEK_START, week_end=WEEK_END, days_covered=7,
        on_time_count=305, late_count=6, cancelled_count=0, on_time_pct=98.07,
        line_stats=line_stats,
    )


async def test_compose_weekly_digest_returns_the_final_text():
    client = _ScriptedLLMClient(
        [LLMResponse(
            text="A strong week: 98% on time across 311 trips, no cancellations.",
            tool_uses=(), stop_reason="end_turn", input_tokens=80, output_tokens=25,
        )]
    )

    text = await compose_weekly_digest(client, _stats(), routes={})

    assert text == "A strong week: 98% on time across 311 trips, no cancellations."


async def test_compose_weekly_digest_calls_with_no_tools():
    # Numbers-computed-by-python, narrate-only -- there is nothing for
    # Haiku to look up, unlike compose_briefing's tool-calling loop.
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    await compose_weekly_digest(client, _stats(), routes={})

    assert client.calls[0]["tools"] is None


async def test_compose_weekly_digest_prompt_states_exact_numbers_not_just_vibes():
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    await compose_weekly_digest(client, _stats(), routes={})

    user_message = client.calls[0]["messages"][0]["content"]
    assert "305" in user_message
    assert "6" in user_message
    assert "98.1" in user_message  # formatted on_time_pct


async def test_compose_weekly_digest_resolves_line_names_from_routes():
    line = LineStats(
        route_id="2-BEG", trip_count=25, on_time_count=20, late_count=5,
        cancelled_count=0, on_time_pct=80.0,
    )
    routes = {"2-BEG": Route(route_id="2-BEG", short_name="Belgrave", long_name="Belgrave - City")}
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    await compose_weekly_digest(client, _stats(line_stats=(line,)), routes=routes)

    user_message = client.calls[0]["messages"][0]["content"]
    assert "Belgrave" in user_message
    assert "2-BEG" not in user_message  # the raw route_id shouldn't leak into the prompt when a name exists


async def test_compose_weekly_digest_falls_back_to_route_id_when_name_unknown():
    line = LineStats(
        route_id="2-UNKNOWN", trip_count=25, on_time_count=25, late_count=0,
        cancelled_count=0, on_time_pct=100.0,
    )
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    await compose_weekly_digest(client, _stats(line_stats=(line,)), routes={})

    user_message = client.calls[0]["messages"][0]["content"]
    assert "2-UNKNOWN" in user_message


async def test_compose_weekly_digest_prompt_includes_days_covered_for_partial_weeks():
    # The prompt must surface a partial window plainly (cold-start /
    # gap-day case, locked 2026-08-01) rather than silently presenting 5
    # days of data as if it were a complete week -- Haiku can only be
    # honest about this if the fact is actually in its input.
    partial_stats = WeeklyStats(
        week_start=WEEK_START, week_end=WEEK_END, days_covered=5,
        on_time_count=200, late_count=4, cancelled_count=0, on_time_pct=98.04,
        line_stats=(),
    )
    client = _ScriptedLLMClient(
        [LLMResponse(text="ok", tool_uses=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)]
    )

    await compose_weekly_digest(client, partial_stats, routes={})

    user_message = client.calls[0]["messages"][0]["content"]
    assert "5 of 7 days" in user_message

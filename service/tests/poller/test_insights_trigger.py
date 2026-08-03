from datetime import date, datetime
from datetime import timezone as tz

import pytest

from traintracker.gtfs.routes import Route
from traintracker.history.store import HistoryStore
from traintracker.insights.store import InsightsStore
from traintracker.poller.insights_trigger import InsightsTrigger, run_insights_cycle
from traintracker.state.completion import TripCompletionEvent

BEG = "2-BEG:"
ROUTES = {BEG: Route(route_id=BEG, short_name="Belgrave", long_name="Belgrave - City")}


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=tz.utc)


# 2026-08-04 09:00 UTC == 2026-08-04 19:00 AEST -- well past the 3am
# day-boundary, so "today" resolves to 2026-08-04 unambiguously.
NOW = _utc(2026, 8, 4, 9, 0)


def _completion_event(service_date_str, route_id=BEG, trip_id="t1"):
    scheduled = datetime(2026, 8, 4, 8, 0, tzinfo=tz.utc)
    return TripCompletionEvent(
        trip_id=trip_id,
        route_id=route_id,
        service_date=service_date_str,
        scheduled_terminus_arrival=scheduled,
        actual_terminus_arrival=scheduled,
        delay_seconds=0,
        status="on_time",
    )


class TestShouldFinalizeYesterday:
    def test_returns_yesterday_when_not_yet_finalized(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json")
        assert trigger.should_finalize_yesterday(NOW) == date(2026, 8, 3)

    def test_mark_finalized_suppresses_repeat_for_same_day(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json")
        yesterday = trigger.should_finalize_yesterday(NOW)
        trigger.mark_finalized(yesterday)
        assert trigger.should_finalize_yesterday(NOW) is None

    def test_advances_the_next_day(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json")
        trigger.mark_finalized(date(2026, 8, 3))
        assert trigger.should_finalize_yesterday(_utc(2026, 8, 5, 9, 0)) == date(2026, 8, 4)

    def test_state_persists_across_instances(self, tmp_path):
        state_path = tmp_path / "state.json"
        InsightsTrigger(state_path).mark_finalized(date(2026, 8, 3))
        reloaded = InsightsTrigger(state_path)
        assert reloaded.should_finalize_yesterday(NOW) is None


class TestShouldRefreshToday:
    def test_true_on_first_check(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json", refresh_interval_seconds=300)
        assert trigger.should_refresh_today(NOW) is True

    def test_false_immediately_after_marking_refreshed(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json", refresh_interval_seconds=300)
        trigger.mark_refreshed(NOW)
        assert trigger.should_refresh_today(NOW) is False

    def test_true_again_once_interval_elapses(self, tmp_path):
        trigger = InsightsTrigger(tmp_path / "state.json", refresh_interval_seconds=300)
        trigger.mark_refreshed(NOW)
        later = _utc(2026, 8, 4, 9, 10)  # 10 minutes later
        assert trigger.should_refresh_today(later) is True


@pytest.mark.asyncio
class TestRunInsightsCycle:
    async def test_finalizes_yesterday_and_persists_a_rollup(self, tmp_path):
        history = HistoryStore(tmp_path / "history")
        history.rotate(_utc(2026, 8, 3, 9, 0))
        history.completion_log.record(_completion_event("2026-08-03"))
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json")

        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

        result = insights_store.read_range((date(2026, 8, 3),))
        assert result.days_covered == (date(2026, 8, 3),)
        [beg] = result.line_rollups
        assert beg.on_time_count == 1

    async def test_finalize_is_idempotent_across_cycles(self, tmp_path):
        history = HistoryStore(tmp_path / "history")
        history.rotate(_utc(2026, 8, 3, 9, 0))
        history.completion_log.record(_completion_event("2026-08-03"))
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json")

        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)
        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

        # Second cycle must not have re-finalized (should_finalize_yesterday
        # returns None the second time) -- no crash, no duplicate rows
        # (record_day is itself idempotent too, so this mostly guards
        # against a wasted re-read, not data corruption).
        result = insights_store.read_range((date(2026, 8, 3),))
        [beg] = result.line_rollups
        assert beg.on_time_count == 1

    async def test_refreshes_today_from_the_still_open_partition(self, tmp_path):
        history = HistoryStore(tmp_path / "history")
        history.rotate(NOW)
        history.completion_log.record(_completion_event("2026-08-04"))
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json")

        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

        result = insights_store.read_range((date(2026, 8, 4),))
        assert result.days_covered == (date(2026, 8, 4),)
        [beg] = result.line_rollups
        assert beg.on_time_count == 1

    async def test_refresh_today_picks_up_new_events_on_a_later_cycle(self, tmp_path):
        history = HistoryStore(tmp_path / "history")
        history.rotate(NOW)
        history.completion_log.record(_completion_event("2026-08-04", trip_id="a"))
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json", refresh_interval_seconds=0)

        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)
        history.completion_log.record(_completion_event("2026-08-04", trip_id="b"))
        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

        result = insights_store.read_range((date(2026, 8, 4),))
        [beg] = result.line_rollups
        assert beg.on_time_count == 2

    async def test_missing_yesterday_partition_is_not_marked_finalized(self, tmp_path):
        # Poller was down that whole service_date -- no partition file at
        # all. Must not mark it finalized (would silently skip forever);
        # next cycle should keep retrying.
        history = HistoryStore(tmp_path / "history")
        history.rotate(NOW)  # only opens today's partition, not yesterday's
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json")

        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

        assert trigger.should_finalize_yesterday(NOW) == date(2026, 8, 3)

    async def test_a_failing_aggregation_does_not_raise(self, tmp_path, monkeypatch):
        history = HistoryStore(tmp_path / "history")
        history.rotate(_utc(2026, 8, 3, 9, 0))
        history.completion_log.record(_completion_event("2026-08-03"))
        insights_store = InsightsStore(tmp_path / "insights.db")
        trigger = InsightsTrigger(tmp_path / "trigger_state.json")

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "traintracker.poller.insights_trigger.aggregate_day", _boom
        )

        # Must not propagate -- same discipline as the weekly digest trigger.
        await run_insights_cycle(trigger, history, insights_store, ROUTES, NOW)

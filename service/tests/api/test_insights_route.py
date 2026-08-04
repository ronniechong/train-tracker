from datetime import date, datetime, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from traintracker.api.app import create_app
from traintracker.gateway.client import GatewayClient
from traintracker.insights.aggregate import (
    DayRollup,
    DelayHistogramDayRollup,
    HourlyDayRollup,
    LineDayRollup,
)
from traintracker.insights.store import InsightsStore
from traintracker.poller.loop import PollerLoop
from traintracker.state.eventhub import InProcessEventHub
from traintracker.state.eventlog import InMemoryEventLog
from traintracker.state.store import StateStore

BEG = "2-BEG:"


def _empty_feed_bytes(timestamp: int) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    return feed.SerializeToString()


async def _client(insights_store: InsightsStore | None) -> httpx.AsyncClient:
    gateway = GatewayClient(api_key="test-key")
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_empty_feed_bytes(1000)))
    )
    store = StateStore(discrepancy_log=InMemoryEventLog(), ghost_log=InMemoryEventLog())
    healthcheck_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    loop = PollerLoop(gateway=gateway, store=store, gap_log=InMemoryEventLog(), healthcheck_client=healthcheck_client)
    await loop.run_cycle(datetime.now(timezone.utc))

    app = create_app(loop=loop, store=store, hub=InProcessEventHub(), insights_store=insights_store)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _rollup(service_date) -> DayRollup:
    return DayRollup(
        service_date=service_date,
        line_rollups=(
            LineDayRollup(
                route_id=BEG, on_time_count=10, late_count=1, cancelled_count=0,
                gap_count=0, replacement_bus_count=2,
            ),
        ),
        hourly_rollups=(
            HourlyDayRollup(route_id=BEG, hour_local=8, completion_count=3),
            HourlyDayRollup(route_id=None, hour_local=8, completion_count=3),
        ),
        histogram_rollup=DelayHistogramDayRollup(
            on_time_count=10, late_5_10_count=1, late_10_plus_count=0, cancelled_count=0, gap_count=0,
        ),
    )


@pytest.mark.asyncio
async def test_returns_503_when_insights_not_configured():
    client = await _client(insights_store=None)
    response = await client.get("/api/insights")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_today_range_returns_line_and_hourly_stats(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    today = date.today()
    store.record_day(_rollup(today))
    client = await _client(insights_store=store)

    response = await client.get("/api/insights", params={"range": "today"})

    assert response.status_code == 200
    body = response.json()
    assert body["range_name"] == "today"
    [line] = body["line_stats"]
    assert line["route_id"] == BEG
    assert line["on_time_count"] == 10
    assert line["replacement_bus_count"] == 2
    assert today.isoformat() in body["generated_at_by_date"]
    assert body["histogram_stats"]["on_time_count"] == 10
    assert body["histogram_stats"]["late_5_10_count"] == 1


@pytest.mark.asyncio
async def test_custom_range_requires_start_and_end(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    client = await _client(insights_store=store)

    response = await client.get("/api/insights", params={"range": "custom"})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_custom_range_with_dates_returns_summed_stats(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    store.record_day(_rollup(date(2026, 8, 1)))
    store.record_day(_rollup(date(2026, 8, 2)))
    client = await _client(insights_store=store)

    response = await client.get(
        "/api/insights",
        params={"range": "custom", "start": "2026-08-01", "end": "2026-08-02"},
    )

    assert response.status_code == 200
    body = response.json()
    assert sorted(body["days_covered"]) == ["2026-08-01", "2026-08-02"]
    [line] = body["line_stats"]
    assert line["on_time_count"] == 20


@pytest.mark.asyncio
async def test_custom_range_returns_unsummed_daily_line_stats(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    store.record_day(_rollup(date(2026, 8, 1)))
    store.record_day(_rollup(date(2026, 8, 2)))
    client = await _client(insights_store=store)

    response = await client.get(
        "/api/insights",
        params={"range": "custom", "start": "2026-08-01", "end": "2026-08-02"},
    )

    assert response.status_code == 200
    body = response.json()
    daily = body["daily_line_stats"]
    assert set(daily.keys()) == {"2026-08-01", "2026-08-02"}
    [line_d1] = daily["2026-08-01"]
    assert line_d1["on_time_count"] == 10  # NOT summed with day 2 (would be 20)


@pytest.mark.asyncio
async def test_unknown_range_name_returns_400(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    client = await _client(insights_store=store)

    response = await client.get("/api/insights", params={"range": "this_week"})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_uncovered_range_returns_empty_not_error(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    client = await _client(insights_store=store)

    response = await client.get("/api/insights", params={"range": "today"})

    assert response.status_code == 200
    body = response.json()
    assert body["days_covered"] == []
    assert body["line_stats"] == []


@pytest.mark.asyncio
async def test_requested_dates_includes_uncovered_days(tmp_path):
    # Only day 2 of a 2-day custom range has a rollup -- requested_dates
    # must still list BOTH days, so a per-day chart can render an
    # explicit gap for day 1 instead of silently omitting it.
    store = InsightsStore(tmp_path / "insights.db")
    store.record_day(_rollup(date(2026, 8, 2)))
    client = await _client(insights_store=store)

    response = await client.get(
        "/api/insights",
        params={"range": "custom", "start": "2026-08-01", "end": "2026-08-02"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested_dates"] == ["2026-08-01", "2026-08-02"]
    assert body["days_covered"] == ["2026-08-02"]

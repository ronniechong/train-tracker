import hashlib
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from traintracker.ai.llm_client import LLMResponse
from traintracker.digests.store import WeeklyDigestStore
from traintracker.gtfs.pinning import PinManifest
from traintracker.gtfs.schedule_cache import PinnedScheduleCache
from traintracker.history.store import HistoryStore
from traintracker.poller.__main__ import _maybe_send_weekly_digest
from traintracker.poller.slack import WEBHOOK_URL_ENV
from traintracker.poller.weekly_digest_trigger import WeeklyDigestTrigger
from traintracker.state.completion import TripCompletionEvent

# A real Monday; the reported week is the 7 days immediately before it.
BOUNDARY_MONDAY = date(2026, 8, 3)
WEEK_START = date(2026, 7, 27)


@pytest.fixture(autouse=True)
def _clear_webhook_env(monkeypatch):
    monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)


def _now_at_boundary() -> datetime:
    # 2026-08-03 09:00 AEST (August is standard time, UTC+10) == 02:00 UTC
    # the day before -- well past the trigger's 8am Melbourne boundary.
    return datetime(2026, 8, 2, 23, 0, tzinfo=timezone.utc)


def _at(d: date, hour=10) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)


class _ScriptedLLMClient:
    def __init__(self, response_text="A solid week overall.", raises=None):
        self._response_text = response_text
        self._raises = raises
        self.calls = 0

    async def complete(self, *, system, messages, tools=None, max_tokens):
        self.calls += 1
        if self._raises:
            raise self._raises
        return LLMResponse(
            text=self._response_text, tool_uses=(), stop_reason="end_turn",
            input_tokens=10, output_tokens=10,
        )


class _RaisingWeeklyDigestStore:
    """Stands in for WeeklyDigestStore.record() failing after Slack
    delivery already happened -- exercises the crash-safety ordering."""

    def record(self, digest):
        raise RuntimeError("disk full")


def _seed_a_weeks_completions(history: HistoryStore) -> None:
    for i in range(7):
        day = WEEK_START + timedelta(days=i)
        history.rotate(_at(day))
        history.completion_log.record(
            TripCompletionEvent(
                trip_id=f"t{i}", route_id="2-BEG", service_date=day.isoformat(),
                scheduled_terminus_arrival=_at(day),
                actual_terminus_arrival=_at(day),
                delay_seconds=0, status="on_time",
            )
        )
    history.close()


def _mock_notify_client(seen: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["posted"] = True
        seen["body"] = request.content
        return httpx.Response(200)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_no_op_when_trigger_says_not_due(tmp_path):
    history = HistoryStore(tmp_path / "history")
    schedule_cache = PinnedScheduleCache(tmp_path / "gtfs", PinManifest(tmp_path / "gtfs" / "pins.json"))
    digest_store = WeeklyDigestStore(tmp_path / "weekly.db")
    trigger = WeeklyDigestTrigger(tmp_path / "trigger_state.json")
    trigger.mark_fired(BOUNDARY_MONDAY)  # already fired for this boundary
    llm_client = _ScriptedLLMClient()
    seen: dict = {}

    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, digest_store, llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )

    assert llm_client.calls == 0
    assert "posted" not in seen
    assert digest_store.list_digests() == []


async def test_fires_generates_posts_and_persists_end_to_end(tmp_path, sample_static_zip_bytes, monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, "https://hooks.invalid/weekly-digest")
    history = HistoryStore(tmp_path / "history")
    _seed_a_weeks_completions(history)
    history = HistoryStore(tmp_path / "history")  # reopen, like the real poller does across the boundary

    gtfs_dir = tmp_path / "gtfs"
    digest = hashlib.sha256(sample_static_zip_bytes).hexdigest()
    gtfs_dir.mkdir()
    (gtfs_dir / f"{digest}.zip").write_bytes(sample_static_zip_bytes)
    manifest = PinManifest(gtfs_dir / "pins.json")
    manifest.pin_digest(date(2026, 8, 2), digest)
    schedule_cache = PinnedScheduleCache(gtfs_dir, manifest)

    digest_store = WeeklyDigestStore(tmp_path / "weekly.db")
    trigger = WeeklyDigestTrigger(tmp_path / "trigger_state.json")
    llm_client = _ScriptedLLMClient(response_text="7 on time, no cancellations this week.")
    seen: dict = {}

    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, digest_store, llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )

    assert llm_client.calls == 1
    assert seen.get("posted") is True
    assert b"7 on time" in seen["body"]

    [stored] = digest_store.list_digests()
    assert stored.record.week_start == WEEK_START
    assert stored.record.on_time_count == 7
    assert stored.record.slack_delivered is True
    assert stored.record.narrative == "7 on time, no cancellations this week."

    # Idempotent: the same boundary must not fire again.
    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, digest_store, llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )
    assert llm_client.calls == 1  # unchanged -- no second call
    assert len(digest_store.list_digests()) == 1


async def test_degrades_gracefully_with_no_pinned_static_snapshot(tmp_path):
    # NoPinnedSnapshotError from routes_for() must not sink the whole
    # digest -- it degrades to raw route_ids rather than skipping.
    history = HistoryStore(tmp_path / "history")
    _seed_a_weeks_completions(history)
    history = HistoryStore(tmp_path / "history")

    gtfs_dir = tmp_path / "gtfs"
    schedule_cache = PinnedScheduleCache(gtfs_dir, PinManifest(gtfs_dir / "pins.json"))  # nothing pinned
    digest_store = WeeklyDigestStore(tmp_path / "weekly.db")
    trigger = WeeklyDigestTrigger(tmp_path / "trigger_state.json")
    llm_client = _ScriptedLLMClient()
    seen: dict = {}

    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, digest_store, llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )

    assert llm_client.calls == 1
    assert len(digest_store.list_digests()) == 1


async def test_llm_failure_prevents_marking_fired_so_next_cycle_retries(tmp_path):
    history = HistoryStore(tmp_path / "history")
    _seed_a_weeks_completions(history)
    history = HistoryStore(tmp_path / "history")

    gtfs_dir = tmp_path / "gtfs"
    schedule_cache = PinnedScheduleCache(gtfs_dir, PinManifest(gtfs_dir / "pins.json"))
    digest_store = WeeklyDigestStore(tmp_path / "weekly.db")
    trigger = WeeklyDigestTrigger(tmp_path / "trigger_state.json")
    llm_client = _ScriptedLLMClient(raises=RuntimeError("anthropic down"))
    seen: dict = {}

    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, digest_store, llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )

    assert "posted" not in seen
    assert digest_store.list_digests() == []
    # Not marked fired -- the next cycle must retry, not lose the week.
    assert trigger.should_fire(_now_at_boundary()) == BOUNDARY_MONDAY


async def test_store_write_failure_after_slack_post_does_not_mark_fired(tmp_path, monkeypatch):
    # The crash-safety ordering itself: if the DB write fails AFTER the
    # Slack post already went out, the boundary must stay unmarked so the
    # next cycle retries (accepting the residual double-post risk this is
    # explicitly documented to accept, milestones/05-ai-layer.md).
    monkeypatch.setenv(WEBHOOK_URL_ENV, "https://hooks.invalid/weekly-digest")
    history = HistoryStore(tmp_path / "history")
    _seed_a_weeks_completions(history)
    history = HistoryStore(tmp_path / "history")

    gtfs_dir = tmp_path / "gtfs"
    schedule_cache = PinnedScheduleCache(gtfs_dir, PinManifest(gtfs_dir / "pins.json"))
    trigger = WeeklyDigestTrigger(tmp_path / "trigger_state.json")
    llm_client = _ScriptedLLMClient()
    seen: dict = {}

    await _maybe_send_weekly_digest(
        trigger, history, schedule_cache, _RaisingWeeklyDigestStore(), llm_client,
        _mock_notify_client(seen), _now_at_boundary(),
    )

    assert seen.get("posted") is True  # the Slack post DID go out before the DB write failed
    assert trigger.should_fire(_now_at_boundary()) == BOUNDARY_MONDAY  # still not marked fired

from datetime import date, datetime
from datetime import timezone as tz
from zoneinfo import ZoneInfo

from traintracker.gtfs.gtfstime import MELBOURNE_TZ
from traintracker.poller.weekly_digest_trigger import WeeklyDigestTrigger


def _melbourne(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(MELBOURNE_TZ))


def test_fires_when_now_is_past_mondays_8am_boundary(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    # 2026-07-27 is a real Monday.
    boundary = trigger.should_fire(_melbourne(2026, 7, 27, 9, 0))
    assert boundary == date(2026, 7, 27)


def test_does_not_fire_before_mondays_8am_boundary(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    # Monday 7:59am -- this week's boundary hasn't been reached yet, so
    # the "most recent" firing Monday is still last week's.
    boundary = trigger.should_fire(_melbourne(2026, 7, 27, 7, 59))
    assert boundary == date(2026, 7, 20)  # the PRIOR Monday


def test_a_weekday_resolves_to_the_most_recent_past_monday(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    boundary = trigger.should_fire(_melbourne(2026, 7, 30, 15, 0))  # a Thursday
    assert boundary == date(2026, 7, 27)


def test_sunday_night_still_resolves_to_the_prior_monday(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    boundary = trigger.should_fire(_melbourne(2026, 8, 2, 23, 59))  # Sunday
    assert boundary == date(2026, 7, 27)


def test_mark_fired_suppresses_should_fire_for_the_same_boundary(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    now = _melbourne(2026, 7, 27, 9, 0)
    boundary = trigger.should_fire(now)
    assert boundary is not None

    trigger.mark_fired(boundary)

    assert trigger.should_fire(now) is None
    # Later the same week, still no re-fire.
    assert trigger.should_fire(_melbourne(2026, 7, 30, 12, 0)) is None


def test_fires_again_on_the_following_mondays_boundary(tmp_path):
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    trigger.mark_fired(trigger.should_fire(_melbourne(2026, 7, 27, 9, 0)))

    next_boundary = trigger.should_fire(_melbourne(2026, 8, 3, 9, 0))

    assert next_boundary == date(2026, 8, 3)


def test_repeated_should_fire_calls_without_marking_keep_returning_the_same_boundary(tmp_path):
    # Simulates the trigger being checked every poll cycle -- it must not
    # advance or self-suppress just from being asked repeatedly; only
    # mark_fired changes persisted state.
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    now = _melbourne(2026, 7, 27, 9, 0)

    first = trigger.should_fire(now)
    second = trigger.should_fire(now)

    assert first == second == date(2026, 7, 27)


def test_state_persists_across_trigger_instances_surviving_a_restart(tmp_path):
    state_path = tmp_path / "state.json"
    first_instance = WeeklyDigestTrigger(state_path)
    boundary = first_instance.should_fire(_melbourne(2026, 7, 27, 9, 0))
    first_instance.mark_fired(boundary)

    reloaded = WeeklyDigestTrigger(state_path)
    assert reloaded.should_fire(_melbourne(2026, 7, 27, 10, 0)) is None


def test_should_fire_accepts_a_utc_now_like_the_real_poll_loop_passes(tmp_path):
    # poller/__main__.py always passes datetime.now(timezone.utc), never a
    # Melbourne-local datetime directly -- the trigger must convert, not
    # assume its caller already did.
    trigger = WeeklyDigestTrigger(tmp_path / "state.json")
    # 2026-07-27 09:00 AEST (UTC+10, non-DST) == 2026-07-26 23:00 UTC.
    utc_now = datetime(2026, 7, 26, 23, 0, tzinfo=tz.utc)
    assert trigger.should_fire(utc_now) == date(2026, 7, 27)

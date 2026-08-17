from datetime import date, datetime
from datetime import timezone as tz

import pytest

from traintracker.insights.ranges import InvalidRangeError, resolve_range

# 2026-08-04 09:00 UTC == 2026-08-04 19:00 AEST, well past the 3am
# day-boundary -- "today" is unambiguously 2026-08-04, a Tuesday.
NOW = datetime(2026, 8, 4, 9, 0, tzinfo=tz.utc)


def test_today_resolves_to_a_single_service_date():
    result = resolve_range("today", NOW)
    assert result.service_dates == (date(2026, 8, 4),)
    assert result.expected_days == 1


def test_yesterday_resolves_to_the_prior_service_date():
    result = resolve_range("yesterday", NOW)
    assert result.service_dates == (date(2026, 8, 3),)
    assert result.expected_days == 1


def test_last7_is_a_rolling_seven_day_window_ending_today():
    # 2026-08-04 is a Tuesday -- the rolling window ignores week boundaries.
    result = resolve_range("last7", NOW)
    assert result.service_dates == tuple(date(2026, 7, d) for d in range(29, 32)) + tuple(
        date(2026, 8, d) for d in range(1, 5)
    )
    assert len(result.service_dates) == 7
    assert result.expected_days == 7


def test_last7_window_crosses_a_month_boundary_correctly():
    sunday_now = datetime(2026, 8, 9, 9, 0, tzinfo=tz.utc)
    result = resolve_range("last7", sunday_now)
    assert result.service_dates == tuple(date(2026, 8, d) for d in range(3, 10))
    assert len(result.service_dates) == 7


def test_last30_is_a_rolling_thirty_day_window_ending_today():
    result = resolve_range("last30", NOW)
    assert result.service_dates[0] == date(2026, 7, 6)
    assert result.service_dates[-1] == date(2026, 8, 4)
    assert len(result.service_dates) == 30
    assert result.expected_days == 30


def test_custom_range_inclusive_of_both_ends():
    result = resolve_range("custom", NOW, date(2026, 8, 1), date(2026, 8, 2))
    assert result.service_dates == (date(2026, 8, 1), date(2026, 8, 2))
    assert result.expected_days == 2


def test_custom_range_clamped_to_today_not_a_future_date():
    result = resolve_range("custom", NOW, date(2026, 8, 1), date(2026, 8, 31))
    assert result.service_dates[-1] == date(2026, 8, 4)
    # expected_days still reflects the caller's requested span, for an
    # honest "you asked for 31, only 4 exist yet" indicator.
    assert result.expected_days == 31


def test_custom_range_missing_dates_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("custom", NOW, None, None)


def test_custom_range_start_after_end_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("custom", NOW, date(2026, 8, 10), date(2026, 8, 1))


def test_custom_range_entirely_in_the_future_returns_empty():
    result = resolve_range("custom", NOW, date(2026, 9, 1), date(2026, 9, 5))
    assert result.service_dates == ()


def test_unknown_range_name_raises():
    with pytest.raises(InvalidRangeError):
        resolve_range("this_week", NOW)

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


def test_last7_is_calendar_iso_week_partial_on_a_tuesday():
    # 2026-08-04 is a Tuesday -- ISO week starts Monday 2026-08-03.
    result = resolve_range("last7", NOW)
    assert result.service_dates == (date(2026, 8, 3), date(2026, 8, 4))
    assert result.expected_days == 7  # full week length, even though partial


def test_last7_is_full_week_on_a_sunday():
    sunday_now = datetime(2026, 8, 9, 9, 0, tzinfo=tz.utc)
    result = resolve_range("last7", sunday_now)
    assert result.service_dates == tuple(date(2026, 8, d) for d in range(3, 10))
    assert len(result.service_dates) == 7


def test_last30_is_calendar_month_partial_early_in_the_month():
    result = resolve_range("last30", NOW)
    assert result.service_dates[0] == date(2026, 8, 1)
    assert result.service_dates[-1] == date(2026, 8, 4)
    assert result.expected_days == 31  # August has 31 days


def test_last30_expected_days_reflects_actual_month_length():
    feb_now = datetime(2026, 2, 15, 9, 0, tzinfo=tz.utc)
    result = resolve_range("last30", feb_now)
    assert result.expected_days == 28  # 2026 is not a leap year


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

"""Resolves the Insights dashboard's global date-range filter (locked
2026-08-04 design review, milestones/08-analytics-insights.md) into a
concrete list of service_dates -- kept separate from `InsightsStore`,
which deliberately "has no opinion on calendar-aligned vs. rolling
ranges" (its own docstring) so this is the one place that decision lives.

**Calendar-aligned, not rolling** (locked): "Last 7 days" = the current
ISO week (Mon-Sun); "Last 30 days" = the current calendar month. Both are
frequently PARTIAL -- picking "Last 7 days" on a Tuesday covers 2 days,
not 7 -- and a range never extends past "today" (there is no data for a
future date). `ResolvedRange.expected_days` is what the UI's
"(N of 7 days)" honesty indicator (locked alongside this decision) is
built from: the full calendar period's length, regardless of how much of
it has actually elapsed.

"Today" here means `service_date_for_instant(now)`, matching this
project's post-midnight-trains-belong-to-the-prior-service-day
convention everywhere else -- not the literal calendar date.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..gtfs.gtfstime import service_date_for_instant

RANGE_NAMES = ("today", "yesterday", "last7", "last30", "custom")


class InvalidRangeError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedRange:
    range_name: str
    service_dates: tuple[date, ...]  # ascending, never past "today", may be partial
    expected_days: int  # the full calendar period's length, for the partial-range indicator


def resolve_range(
    range_name: str,
    now: datetime,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> ResolvedRange:
    today = service_date_for_instant(now)

    if range_name == "today":
        return ResolvedRange("today", (today,), expected_days=1)

    if range_name == "yesterday":
        return ResolvedRange("yesterday", (today - timedelta(days=1),), expected_days=1)

    if range_name == "last7":
        monday = today - timedelta(days=today.weekday())
        dates = tuple(monday + timedelta(days=i) for i in range((today - monday).days + 1))
        return ResolvedRange("last7", dates, expected_days=7)

    if range_name == "last30":
        first_of_month = today.replace(day=1)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        dates = tuple(
            first_of_month + timedelta(days=i) for i in range((today - first_of_month).days + 1)
        )
        return ResolvedRange("last30", dates, expected_days=days_in_month)

    if range_name == "custom":
        if custom_start is None or custom_end is None:
            raise InvalidRangeError("custom range requires both start and end dates")
        if custom_start > custom_end:
            raise InvalidRangeError("custom range start must not be after end")
        # Also never extends past "today", same as every other range --
        # a caller passing a future end date gets today as the effective
        # end, not an empty/nonsensical future window.
        effective_end = min(custom_end, today)
        if custom_start > effective_end:
            return ResolvedRange("custom", (), expected_days=(custom_end - custom_start).days + 1)
        span = (effective_end - custom_start).days + 1
        dates = tuple(custom_start + timedelta(days=i) for i in range(span))
        return ResolvedRange("custom", dates, expected_days=(custom_end - custom_start).days + 1)

    raise InvalidRangeError(f"unknown range: {range_name!r}, expected one of {RANGE_NAMES}")

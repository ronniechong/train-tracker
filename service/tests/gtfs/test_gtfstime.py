from datetime import date, datetime, timedelta, timezone

import pytest

from traintracker.gtfs.gtfstime import (
    gtfs_time_to_utc,
    parse_gtfs_time,
    service_date_boundary_utc,
    service_date_for_instant,
    service_day_start_utc,
)


def test_parse_gtfs_time_normal():
    assert parse_gtfs_time("05:30:00") == timedelta(hours=5, minutes=30)


def test_parse_gtfs_time_past_midnight():
    # GTFS allows hours >= 24 for trips that run past midnight.
    assert parse_gtfs_time("25:15:00") == timedelta(hours=25, minutes=15)


def test_parse_gtfs_time_rejects_garbage():
    with pytest.raises(ValueError):
        parse_gtfs_time("not-a-time")


def test_ordinary_day_no_dst():
    # A plain winter service_date, well away from any transition.
    result = gtfs_time_to_utc(date(2026, 7, 20), "05:30:00")
    # AEST is UTC+10 in July.
    assert result == datetime(2026, 7, 19, 19, 30, 0, tzinfo=timezone.utc)


def test_spring_forward_2026_10_04():
    # Melbourne clocks jump 2:00am -> 3:00am on 2026-10-04. A trip scheduled
    # as "26:30:00" from service_date 2026-10-03 nominally targets a local
    # time (2:30am) that does not exist that day. The elapsed-duration
    # anchor must still produce a single, correct UTC instant rather than
    # raising or silently picking an arbitrary interpretation.
    result = gtfs_time_to_utc(date(2026, 10, 3), "26:30:00")
    assert result == datetime(2026, 10, 3, 16, 30, 0, tzinfo=timezone.utc)
    # Sanity-check by converting back to local time: elapsed 26.5h past the
    # start of Oct 3 lands at 3:30am AEDT on Oct 4, not the naive 2:30am,
    # because the skipped hour was never available to land in.
    from zoneinfo import ZoneInfo

    local = result.astimezone(ZoneInfo("Australia/Melbourne"))
    assert local == datetime(2026, 10, 4, 3, 30, 0, tzinfo=ZoneInfo("Australia/Melbourne"))


def test_fall_back_2026_04_05():
    # Melbourne clocks fall back 3:00am -> 2:00am on 2026-04-05, so 2:00-3:00am
    # local occurs twice. A trip scheduled as "26:30:00" from service_date
    # 2026-04-04 must still resolve to exactly one unambiguous UTC instant.
    result = gtfs_time_to_utc(date(2026, 4, 4), "26:30:00")
    assert result == datetime(2026, 4, 4, 15, 30, 0, tzinfo=timezone.utc)


def test_service_day_start_is_noon_minus_12h_not_literal_midnight():
    # On an ordinary day these coincide; the point of anchoring at noon only
    # matters on a transition day, exercised by the two tests above via
    # gtfs_time_to_utc. This test just pins the anchor's own value.
    start = service_day_start_utc(date(2026, 7, 20))
    assert start == datetime(2026, 7, 19, 14, 0, 0, tzinfo=timezone.utc)


def test_service_date_for_instant_before_boundary_is_previous_day():
    # 1:00am local on 2026-07-21 is before the default 3am boundary, so it
    # still belongs to the 2026-07-20 service day (a post-midnight train).
    instant = datetime(2026, 7, 20, 15, 0, 0, tzinfo=timezone.utc)  # 1am AEST Jul 21
    assert service_date_for_instant(instant) == date(2026, 7, 20)


def test_service_date_for_instant_after_boundary_is_same_day():
    instant = datetime(2026, 7, 20, 0, 0, 0, tzinfo=timezone.utc)  # 10am AEST Jul 20
    assert service_date_for_instant(instant) == date(2026, 7, 20)


def test_service_date_for_instant_requires_aware_datetime():
    with pytest.raises(ValueError):
        service_date_for_instant(datetime(2026, 7, 20, 0, 0, 0))


def test_service_date_boundary_utc_is_the_inverse_of_service_date_for_instant():
    # 3am AEST on 2026-07-21 is UTC+10 -> 17:00 UTC on 2026-07-20.
    boundary = service_date_boundary_utc(date(2026, 7, 21))
    assert boundary == datetime(2026, 7, 20, 17, 0, 0, tzinfo=timezone.utc)
    # An instant one second before the boundary still belongs to the prior
    # service_date; one second at/after it has rolled over to this one.
    assert service_date_for_instant(boundary - timedelta(seconds=1)) == date(2026, 7, 20)
    assert service_date_for_instant(boundary) == date(2026, 7, 21)


def test_service_date_boundary_utc_across_dst_transition():
    # 3am AEDT on the spring-forward date itself is still a normal, existing
    # local time (only 2-3am is skipped), so this should resolve cleanly.
    boundary = service_date_boundary_utc(date(2026, 10, 4))
    assert service_date_for_instant(boundary) == date(2026, 10, 4)

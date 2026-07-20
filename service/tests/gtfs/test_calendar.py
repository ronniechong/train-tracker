from datetime import date

from traintracker.gtfs.calendar import GtfsCalendar


def test_weekday_service_runs_on_monday(sample_snapshot):
    active = sample_snapshot.calendar.active_service_ids(date(2026, 7, 20))  # Monday
    assert "WEEKDAY" in active
    assert "WEEKEND" not in active


def test_weekend_service_runs_on_saturday(sample_snapshot):
    active = sample_snapshot.calendar.active_service_ids(date(2026, 7, 25))  # Saturday
    assert "WEEKEND" in active
    assert "WEEKDAY" not in active


def test_calendar_dates_exception_overrides_weekly_rule(sample_snapshot):
    # 2026-04-06 is a Monday but the fixture's calendar_dates.txt removes
    # WEEKDAY and adds WEEKEND for that specific date (a public holiday
    # running a weekend-style timetable).
    active = sample_snapshot.calendar.active_service_ids(date(2026, 4, 6))
    assert "WEEKDAY" not in active
    assert "WEEKEND" in active


def test_outside_date_range_is_inactive():
    calendar = GtfsCalendar.from_csv(
        calendar_txt=(
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\nWEEKDAY,1,1,1,1,1,0,0,20260101,20260630\n"
        ),
        calendar_dates_txt="service_id,date,exception_type\n",
    )
    assert "WEEKDAY" not in calendar.active_service_ids(date(2026, 7, 20))

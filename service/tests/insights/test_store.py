from datetime import date, datetime, timezone

from traintracker.insights.aggregate import (
    DayRollup,
    DelayHistogramDayRollup,
    HourlyDayRollup,
    LineDayRollup,
)
from traintracker.insights.store import InsightsStore

BEG = "2-BEG:"
SBY = "2-SBY:"


def _rollup(
    service_date, beg_on_time=10, beg_r_count=0, sby_on_time=5, histogram=None
) -> DayRollup:
    return DayRollup(
        service_date=service_date,
        line_rollups=(
            LineDayRollup(
                route_id=BEG, on_time_count=beg_on_time, late_count=1, cancelled_count=0,
                gap_count=0, replacement_bus_count=beg_r_count,
            ),
            LineDayRollup(
                route_id=SBY, on_time_count=sby_on_time, late_count=0, cancelled_count=1,
                gap_count=0, replacement_bus_count=0,
            ),
        ),
        hourly_rollups=(
            HourlyDayRollup(route_id=BEG, hour_local=8, completion_count=3),
            HourlyDayRollup(route_id=None, hour_local=8, completion_count=3),
        ),
        histogram_rollup=histogram
        or DelayHistogramDayRollup(
            on_time_count=beg_on_time + sby_on_time, late_5_10_count=1, late_10_plus_count=0,
            cancelled_count=1, gap_count=0,
        ),
    )


def test_record_day_then_read_range_round_trips_single_day(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(_rollup(d))

    result = store.read_range((d,))

    assert result.days_covered == (d,)
    [beg, sby] = sorted(result.line_rollups, key=lambda r: r.route_id)
    assert beg.route_id == BEG
    assert beg.on_time_count == 10
    assert beg.replacement_bus_count == 0
    assert sby.on_time_count == 5
    assert sby.cancelled_count == 1


def test_read_range_sums_across_multiple_days(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(_rollup(d1, beg_on_time=10))
    store.record_day(_rollup(d2, beg_on_time=7))

    result = store.read_range((d1, d2))

    assert result.days_covered == (d1, d2)
    [beg] = [r for r in result.line_rollups if r.route_id == BEG]
    assert beg.on_time_count == 17


def test_read_range_reports_uncovered_dates_as_missing_not_zero(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(_rollup(d1))

    result = store.read_range((d1, d2))

    # d2 has no persisted rollup at all -- an unknown, distinct from a
    # covered day with genuinely zero completions.
    assert result.days_covered == (d1,)


def test_read_range_empty_input_returns_empty_result_not_crash(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    result = store.read_range(())
    assert result.days_covered == ()
    assert result.line_rollups == ()
    assert result.hourly_rollups == ()


def test_record_day_is_idempotent_reruns_replace_not_append(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(_rollup(d, beg_on_time=5))
    store.record_day(_rollup(d, beg_on_time=8))  # e.g. refreshing "today" with more events

    result = store.read_range((d,))
    [beg] = [r for r in result.line_rollups if r.route_id == BEG]
    assert beg.on_time_count == 8


def test_hourly_rollups_round_trip_including_network_wide_row(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(_rollup(d))

    result = store.read_range((d,))

    per_route = [r for r in result.hourly_rollups if r.route_id == BEG]
    network = [r for r in result.hourly_rollups if r.route_id is None]
    assert per_route == [HourlyDayRollup(route_id=BEG, hour_local=8, completion_count=3)]
    assert network == [HourlyDayRollup(route_id=None, hour_local=8, completion_count=3)]


def test_record_day_stamps_generated_at_and_read_range_surfaces_it(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    before = datetime.now(timezone.utc)
    store.record_day(_rollup(d))
    after = datetime.now(timezone.utc)

    result = store.read_range((d,))

    assert d in result.generated_at_by_date
    generated_at = result.generated_at_by_date[d]
    assert generated_at.tzinfo is not None
    assert before <= generated_at <= after


def test_generated_at_refreshes_on_reruns_for_the_same_date(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(_rollup(d, beg_on_time=5))
    first_generated_at = store.read_range((d,)).generated_at_by_date[d]

    store.record_day(_rollup(d, beg_on_time=8))  # e.g. a later "today" refresh
    second_generated_at = store.read_range((d,)).generated_at_by_date[d]

    assert second_generated_at >= first_generated_at


def test_generated_at_by_date_omits_dates_with_no_rollup(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(_rollup(d1))

    result = store.read_range((d1, d2))

    assert d1 in result.generated_at_by_date
    assert d2 not in result.generated_at_by_date


def test_empty_range_returns_empty_generated_at_map(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    result = store.read_range(())
    assert result.generated_at_by_date == {}


def test_daily_line_rollups_are_not_summed_across_days(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(_rollup(d1, beg_on_time=10))
    store.record_day(_rollup(d2, beg_on_time=7))

    result = store.read_range((d1, d2))

    assert set(result.daily_line_rollups.keys()) == {d1, d2}
    [beg_d1] = [r for r in result.daily_line_rollups[d1] if r.route_id == BEG]
    [beg_d2] = [r for r in result.daily_line_rollups[d2] if r.route_id == BEG]
    assert beg_d1.on_time_count == 10
    assert beg_d2.on_time_count == 7
    # The summed field is unaffected -- both views come from one read_range call.
    [beg_summed] = [r for r in result.line_rollups if r.route_id == BEG]
    assert beg_summed.on_time_count == 17


def test_daily_line_rollups_empty_for_uncovered_dates(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(_rollup(d1))

    result = store.read_range((d1, d2))

    assert d1 in result.daily_line_rollups
    assert d2 not in result.daily_line_rollups


def test_empty_range_returns_empty_daily_line_rollups(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    result = store.read_range(())
    assert result.daily_line_rollups == {}


def test_histogram_rollup_round_trips_single_day(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(
        _rollup(
            d,
            histogram=DelayHistogramDayRollup(
                on_time_count=100, late_5_10_count=5, late_10_plus_count=2,
                cancelled_count=1, gap_count=1,
            ),
        )
    )

    result = store.read_range((d,))

    assert result.histogram_rollup == DelayHistogramDayRollup(
        on_time_count=100, late_5_10_count=5, late_10_plus_count=2, cancelled_count=1, gap_count=1,
    )


def test_histogram_rollup_sums_across_multiple_days(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d1, d2 = date(2026, 8, 1), date(2026, 8, 2)
    store.record_day(
        _rollup(d1, histogram=DelayHistogramDayRollup(10, 1, 0, 0, 0))
    )
    store.record_day(
        _rollup(d2, histogram=DelayHistogramDayRollup(20, 2, 1, 1, 0))
    )

    result = store.read_range((d1, d2))

    assert result.histogram_rollup == DelayHistogramDayRollup(
        on_time_count=30, late_5_10_count=3, late_10_plus_count=1, cancelled_count=1, gap_count=0,
    )


def test_histogram_rollup_is_idempotent_reruns_replace_not_double_count(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    d = date(2026, 8, 4)
    store.record_day(_rollup(d, histogram=DelayHistogramDayRollup(10, 1, 0, 0, 0)))
    store.record_day(_rollup(d, histogram=DelayHistogramDayRollup(15, 2, 0, 0, 0)))

    result = store.read_range((d,))

    assert result.histogram_rollup.on_time_count == 15


def test_empty_range_returns_zeroed_histogram_rollup(tmp_path):
    store = InsightsStore(tmp_path / "insights.db")
    result = store.read_range(())
    assert result.histogram_rollup == DelayHistogramDayRollup(0, 0, 0, 0, 0)

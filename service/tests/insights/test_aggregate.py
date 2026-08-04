from datetime import date, datetime, timezone

from traintracker.gtfs.routes import Route
from traintracker.insights.aggregate import aggregate_day
from traintracker.state.completion import TripCompletionEvent

SERVICE_DATE = date(2026, 8, 4)
BEG = "2-BEG:"
BEG_R = "2-BEG-R:"
SBY = "2-SBY:"

ROUTES = {
    BEG: Route(route_id=BEG, short_name="Belgrave", long_name="Belgrave - City"),
    BEG_R: Route(route_id=BEG_R, short_name="Replacement Bus", long_name="Belgrave - City"),
    SBY: Route(route_id=SBY, short_name="Sunbury", long_name="Sunbury - City"),
}


def _event(
    route_id,
    status,
    trip_id="t1",
    hour_utc=0,
    delay_seconds=None,
) -> TripCompletionEvent:
    scheduled = datetime(2026, 8, 4, hour_utc, 0, tzinfo=timezone.utc)
    actual = scheduled if status in ("on_time", "late") else None
    return TripCompletionEvent(
        trip_id=trip_id,
        route_id=route_id,
        service_date=SERVICE_DATE.isoformat(),
        scheduled_terminus_arrival=scheduled,
        actual_terminus_arrival=actual,
        delay_seconds=delay_seconds,
        status=status,
    )


def test_real_line_status_counts_exclude_replacement_bus_rows():
    events = (
        _event(BEG, "on_time", trip_id="a"),
        _event(BEG, "cancelled", trip_id="b"),
        _event(BEG_R, "on_time", trip_id="c"),  # bus trip -- must not count toward BEG
        _event(BEG_R, "on_time", trip_id="d"),
    )
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)

    [beg] = [r for r in rollup.line_rollups if r.route_id == BEG]
    assert beg.on_time_count == 1
    assert beg.cancelled_count == 1
    assert beg.late_count == 0
    assert beg.gap_count == 0
    # The -R events are NOT merged into on_time/late/cancelled -- only
    # surfaced via the separate replacement_bus_count field.
    assert beg.replacement_bus_count == 2
    # -R never appears as its own line_rollups row.
    assert BEG_R not in [r.route_id for r in rollup.line_rollups]


def test_line_with_no_replacement_bus_activity_has_zero_count():
    events = (_event(SBY, "on_time"),)
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)

    [sby] = [r for r in rollup.line_rollups if r.route_id == SBY]
    assert sby.replacement_bus_count == 0


def test_hourly_rollup_buckets_by_melbourne_local_hour_not_utc():
    # 2026-08-04 14:00 UTC = 2026-08-05 00:00 AEST (UTC+10, no DST in Aug).
    events = (_event(BEG, "on_time", hour_utc=14),)
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)

    [beg_hourly] = [
        r for r in rollup.hourly_rollups if r.route_id == BEG
    ]
    assert beg_hourly.hour_local == 0
    assert beg_hourly.completion_count == 1


def test_hourly_rollup_excludes_replacement_bus_and_includes_network_wide():
    events = (
        _event(BEG, "on_time", hour_utc=0, trip_id="a"),
        _event(SBY, "on_time", hour_utc=0, trip_id="b"),
        _event(BEG_R, "on_time", hour_utc=0, trip_id="c"),
    )
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)

    assert BEG_R not in [r.route_id for r in rollup.hourly_rollups]
    [network] = [
        r for r in rollup.hourly_rollups if r.route_id is None and r.hour_local == 10
    ]
    # 00:00 UTC == 10:00 AEST
    assert network.completion_count == 2


def test_events_with_no_route_id_are_skipped_not_crashed_on():
    events = (_event(None, "on_time"),)
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)
    assert rollup.line_rollups == ()
    assert rollup.hourly_rollups == ()


def test_undetermined_gap_counted_separately_never_folded_in():
    events = (_event(BEG, "undetermined_gap"),)
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)
    [beg] = rollup.line_rollups
    assert beg.gap_count == 1
    assert beg.on_time_count == 0


def test_histogram_buckets_late_events_by_delay_threshold():
    events = (
        _event(BEG, "on_time", trip_id="a", delay_seconds=100),
        _event(BEG, "late", trip_id="b", delay_seconds=400),  # < 600s -> late_5_10
        _event(BEG, "late", trip_id="c", delay_seconds=900),  # >= 600s -> late_10_plus
        _event(BEG, "cancelled", trip_id="d"),
        _event(BEG, "undetermined_gap", trip_id="e"),
    )
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)

    h = rollup.histogram_rollup
    assert h.on_time_count == 1
    assert h.late_5_10_count == 1
    assert h.late_10_plus_count == 1
    assert h.cancelled_count == 1
    assert h.gap_count == 1


def test_histogram_late_boundary_is_inclusive_of_10_minutes():
    events = (_event(BEG, "late", delay_seconds=600),)  # exactly 10:00
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)
    assert rollup.histogram_rollup.late_10_plus_count == 1
    assert rollup.histogram_rollup.late_5_10_count == 0


def test_histogram_excludes_replacement_bus_events():
    events = (
        _event(BEG, "on_time", trip_id="a"),
        _event(BEG_R, "on_time", trip_id="b"),
    )
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)
    assert rollup.histogram_rollup.on_time_count == 1


def test_histogram_is_network_wide_summed_across_lines():
    events = (
        _event(BEG, "on_time", trip_id="a"),
        _event(SBY, "on_time", trip_id="b"),
    )
    rollup = aggregate_day(SERVICE_DATE, events, ROUTES)
    assert rollup.histogram_rollup.on_time_count == 2

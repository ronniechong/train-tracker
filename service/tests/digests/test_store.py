from datetime import date, datetime, timezone

from traintracker.digests.store import LineStat, WeeklyDigestRecord, WeeklyDigestStore


def _record(
    week_start=date(2026, 7, 27),
    week_end=date(2026, 8, 2),
    days_covered=7,
    on_time_count=305,
    late_count=6,
    cancelled_count=0,
    on_time_pct=98.07,
    narrative="A solid week overall.",
    slack_delivered=True,
    line_stats=(),
) -> WeeklyDigestRecord:
    return WeeklyDigestRecord(
        week_start=week_start, week_end=week_end, days_covered=days_covered,
        on_time_count=on_time_count, late_count=late_count, cancelled_count=cancelled_count,
        on_time_pct=on_time_pct, narrative=narrative, slack_delivered=slack_delivered,
        line_stats=line_stats,
    )


def test_record_then_list_round_trips_all_fields(tmp_path):
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    line = LineStat(
        route_id="2-BEG", trip_count=42, on_time_count=40, late_count=2,
        cancelled_count=0, on_time_pct=95.24,
    )
    stored = store.record(_record(line_stats=(line,)))

    assert stored.id is not None
    assert isinstance(stored.generated_at, datetime)
    assert stored.generated_at.tzinfo is not None

    [listed] = store.list_digests()
    assert listed.id == stored.id
    assert listed.record.week_start == date(2026, 7, 27)
    assert listed.record.week_end == date(2026, 8, 2)
    assert listed.record.days_covered == 7
    assert listed.record.on_time_count == 305
    assert listed.record.late_count == 6
    assert listed.record.cancelled_count == 0
    assert listed.record.on_time_pct == 98.07
    assert listed.record.narrative == "A solid week overall."
    assert listed.record.slack_delivered is True
    assert listed.record.line_stats == (line,)


def test_digest_with_no_line_stats_round_trips_empty_tuple(tmp_path):
    # Must not crash on an empty line_stats tuple in either direction.
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    store.record(_record(line_stats=()))

    [listed] = store.list_digests()
    assert listed.record.line_stats == ()


def test_list_digests_orders_most_recent_week_first(tmp_path):
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    store.record(_record(week_start=date(2026, 7, 13), week_end=date(2026, 7, 19)))
    store.record(_record(week_start=date(2026, 7, 27), week_end=date(2026, 8, 2)))
    store.record(_record(week_start=date(2026, 7, 20), week_end=date(2026, 7, 26)))

    listed = store.list_digests()
    assert [d.record.week_start for d in listed] == [
        date(2026, 7, 27), date(2026, 7, 20), date(2026, 7, 13),
    ]


def test_list_digests_respects_limit(tmp_path):
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    for week in range(3):
        store.record(_record(week_start=date(2026, 7, 6 + week * 7), week_end=date(2026, 7, 12 + week * 7)))

    assert len(store.list_digests(limit=2)) == 2
    assert len(store.list_digests(limit=100)) == 3


def test_digests_persist_across_store_instances(tmp_path):
    db_path = tmp_path / "weekly.db"
    WeeklyDigestStore(db_path).record(_record())

    reopened = WeeklyDigestStore(db_path)
    assert len(reopened.list_digests()) == 1


def test_slack_delivered_false_round_trips_correctly(tmp_path):
    # False must not silently become True through int coercion
    # (SQLite has no native bool type).
    store = WeeklyDigestStore(tmp_path / "weekly.db")
    store.record(_record(slack_delivered=False))

    [listed] = store.list_digests()
    assert listed.record.slack_delivered is False

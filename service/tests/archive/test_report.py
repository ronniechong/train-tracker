from datetime import date, datetime, timedelta, timezone

from traintracker.archive.report import (
    GapReportEntry,
    append_gap_report,
    prune_gap_report,
    read_gap_report,
)


def test_round_trips_through_jsonl(tmp_path):
    report_path = tmp_path / "report.jsonl"
    entry = GapReportEntry(
        service_date=date(2026, 7, 20),
        detected_at=datetime(2026, 7, 21, 3, 30, 0, tzinfo=timezone.utc),
        reason="restored_from_backup",
        recovered=True,
        permanent=False,
    )
    append_gap_report(report_path, entry)

    result = read_gap_report(report_path)
    assert result == [entry]


def test_missing_report_file_reads_as_empty(tmp_path):
    assert read_gap_report(tmp_path / "does-not-exist.jsonl") == []


def test_multiple_entries_append_in_order(tmp_path):
    report_path = tmp_path / "report.jsonl"
    for day in (20, 21, 22):
        append_gap_report(
            report_path,
            GapReportEntry(
                service_date=date(2026, 7, day),
                detected_at=datetime(2026, 7, day, 3, 30, 0, tzinfo=timezone.utc),
                reason="missing_or_corrupt_partition_and_backup",
                recovered=False,
                permanent=False,
            ),
        )
    result = read_gap_report(report_path)
    assert [e.service_date.day for e in result] == [20, 21, 22]


def test_prune_removes_entries_older_than_retention(tmp_path):
    report_path = tmp_path / "report.jsonl"
    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
    old_entry = GapReportEntry(
        service_date=date(2025, 1, 1),
        detected_at=now - timedelta(days=200),  # older than 182-day default
        reason="missing_or_corrupt_partition_and_backup",
        recovered=False, permanent=True,
    )
    recent_entry = GapReportEntry(
        service_date=date(2026, 8, 1),
        detected_at=now - timedelta(days=5),
        reason="restored_from_backup",
        recovered=True, permanent=False,
    )
    append_gap_report(report_path, old_entry)
    append_gap_report(report_path, recent_entry)

    removed = prune_gap_report(report_path, now)

    assert removed == 1
    result = read_gap_report(report_path)
    assert result == [recent_entry]


def test_prune_is_a_noop_on_a_missing_report(tmp_path):
    assert prune_gap_report(tmp_path / "does-not-exist.jsonl", datetime.now(timezone.utc)) == 0

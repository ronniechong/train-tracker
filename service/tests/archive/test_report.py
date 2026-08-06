from datetime import date, datetime, timezone

from traintracker.archive.report import GapReportEntry, append_gap_report, read_gap_report


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

from datetime import date, datetime, timezone

from traintracker.archive.metrics import render_textfile, write_textfile_metrics
from traintracker.archive.run import ArchiveRunResult

NOW = datetime(2026, 8, 7, 3, 30, 0, tzinfo=timezone.utc)


def _result(**overrides):
    defaults = dict(
        archived=(),
        failed=(),
        recovered_from_backup=(),
        drift_findings=(),
        upload_retry_failures=0,
        latest_archived_date=None,
    )
    defaults.update(overrides)
    return ArchiveRunResult(**defaults)


def test_clean_run_reports_zero_pending():
    text = render_textfile(_result(archived=(date(2026, 8, 6),)), NOW)
    assert "archive_days_pending 0" in text
    assert "archive_days_pending_oldest_age_days 0" in text
    assert f"archive_last_run_timestamp_seconds {NOW.timestamp()}" in text
    assert "archive_upload_retry_failures_total 0" in text


def test_pending_days_report_oldest_age():
    failed = (date(2026, 7, 10), date(2026, 8, 1))  # 28 and 6 days old
    text = render_textfile(_result(failed=failed, upload_retry_failures=3), NOW)
    assert "archive_days_pending 2" in text
    assert "archive_days_pending_oldest_age_days 28" in text
    assert "archive_upload_retry_failures_total 3" in text


def test_write_textfile_metrics_creates_parent_and_is_readable(tmp_path):
    path = tmp_path / "metrics" / "archiver.prom"
    write_textfile_metrics(path, _result(), NOW)
    assert path.exists()
    assert "archive_days_pending 0" in path.read_text()


def test_write_textfile_metrics_overwrites_atomically(tmp_path):
    path = tmp_path / "archiver.prom"
    write_textfile_metrics(path, _result(failed=(date(2026, 7, 1),)), NOW)
    write_textfile_metrics(path, _result(), NOW)
    text = path.read_text()
    assert "archive_days_pending 0" in text
    assert not (path.parent / (path.name + ".tmp")).exists()

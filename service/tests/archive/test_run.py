from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from traintracker.archive.report import read_gap_report
from traintracker.archive.run import SAFETY_NET_DAYS, run_archive_pass
from traintracker.history.store import HistoryStore
from traintracker.state.merge import DiscrepancyEvent

# Far enough past any real service_date used below that every partition is
# unambiguously closed (3am boundary + buffer).
NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _at(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)


def _make_closed_partition(history_dir, service_date, trip_id="t1"):
    store = HistoryStore(history_dir)
    store.rotate(_at(service_date.year, service_date.month, service_date.day))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id=trip_id, observed_at=_at(*service_date.timetuple()[:3]),
            discrepancy_type="vp_without_tu", tu_value=None, vp_value="3",
        )
    )
    store.close()


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_archives_a_new_closed_day(mock_archived_days, mock_upload_day, tmp_path):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    _make_closed_partition(history_dir, date(2026, 7, 20))
    mock_archived_days.return_value = {}

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.archived == (date(2026, 7, 20),)
    assert result.failed == ()
    mock_upload_day.assert_called_once()


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_drift_findings_surface_but_do_not_block_archiving(
    mock_archived_days, mock_upload_day, tmp_path
):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    store = HistoryStore(history_dir)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="a_brand_new_type",
            tu_value=None, vp_value="3",
        )
    )
    store.close()
    mock_archived_days.return_value = {}

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.archived == (date(2026, 7, 20),)
    assert len(result.drift_findings) == 1
    assert result.drift_findings[0].unknown_values == frozenset({"a_brand_new_type"})


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_already_archived_day_is_skipped(mock_archived_days, mock_upload_day, tmp_path):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    _make_closed_partition(history_dir, date(2026, 7, 20))
    mock_archived_days.return_value = {
        "discrepancy_events": {date(2026, 7, 20)},
        "ghost_events": {date(2026, 7, 20)},
        "poll_gap_events": {date(2026, 7, 20)},
        "trip_completion_events": {date(2026, 7, 20)},
        "delay_observation_events": {date(2026, 7, 20)},
    }

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.archived == ()
    mock_upload_day.assert_not_called()


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_self_heals_from_backup_when_live_partition_is_missing(
    mock_archived_days, mock_upload_day, tmp_path
):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    backup_dir.mkdir(parents=True)
    _make_closed_partition(backup_dir, date(2026, 7, 20))  # only exists in backup
    history_dir.mkdir(parents=True)
    mock_archived_days.return_value = {}

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.archived == (date(2026, 7, 20),)
    assert result.recovered_from_backup == (date(2026, 7, 20),)
    report = read_gap_report(tmp_path / "report.jsonl")
    assert len(report) == 1
    assert report[0].reason == "restored_from_backup"
    assert report[0].recovered is True


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_recent_unrecoverable_day_is_not_yet_permanent(mock_archived_days, mock_upload_day, tmp_path):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    history_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    # Create an empty, corrupt "partition" file with a valid closed-day name
    # but no backup counterpart -- neither live nor backup is usable.
    (history_dir / "2026-08-25.db").write_bytes(b"not a real sqlite file")
    mock_archived_days.return_value = {}

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.failed == (date(2026, 8, 25),)
    report = read_gap_report(tmp_path / "report.jsonl")
    assert len(report) == 1
    assert report[0].permanent is False  # (2026-09-01 - 2026-08-25).days == 7, well under 23
    mock_upload_day.assert_not_called()


@patch("traintracker.archive.run.upload_day")
@patch("traintracker.archive.run.archived_days")
def test_old_unrecoverable_day_is_marked_permanent(mock_archived_days, mock_upload_day, tmp_path):
    history_dir, backup_dir, staging_dir = tmp_path / "history", tmp_path / "backup", tmp_path / "staging"
    history_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    old_date = date(2026, 6, 1)  # well over SAFETY_NET_DAYS behind NOW (2026-09-01)
    assert (NOW.date() - old_date).days > SAFETY_NET_DAYS
    (history_dir / f"{old_date.isoformat()}.db").write_bytes(b"not a real sqlite file")
    mock_archived_days.return_value = {}

    result = run_archive_pass(
        history_dir=history_dir, backup_dir=backup_dir, staging_dir=staging_dir,
        report_path=tmp_path / "report.jsonl", repo_id="whitemanjuu/train-tracker",
        token="fake-token", now=NOW,
    )

    assert result.failed == (old_date,)
    report = read_gap_report(tmp_path / "report.jsonl")
    assert report[0].permanent is True

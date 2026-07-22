from datetime import date, datetime, timedelta, timezone

import pytest

from traintracker.history.retention import (
    RETENTION_DAYS,
    apply_retention,
    is_partition_closed,
    partition_service_date,
)


def _touch(path, name):
    (path / name).write_text("")


def test_partition_service_date_parses_the_filename(tmp_path):
    path = tmp_path / "2026-07-20.db"
    assert partition_service_date(path) == date(2026, 7, 20)


def test_partition_service_date_rejects_unrecognised_names(tmp_path):
    with pytest.raises(ValueError):
        partition_service_date(tmp_path / "not-a-date.db")


def test_is_partition_closed_false_just_before_the_boundary():
    # 3am AEST on 2026-07-21 is 17:00 UTC on 2026-07-20 -- the instant a
    # 2026-07-20 partition becomes closeable, before the buffer.
    almost = datetime(2026, 7, 20, 16, 59, 0, tzinfo=timezone.utc)
    assert is_partition_closed(date(2026, 7, 20), almost) is False


def test_is_partition_closed_false_within_the_buffer_window():
    just_past_boundary = datetime(2026, 7, 20, 17, 10, 0, tzinfo=timezone.utc)
    assert is_partition_closed(date(2026, 7, 20), just_past_boundary) is False


def test_is_partition_closed_true_once_boundary_plus_buffer_has_passed():
    past_buffer = datetime(2026, 7, 20, 17, 30, 0, tzinfo=timezone.utc)
    assert is_partition_closed(date(2026, 7, 20), past_buffer) is True


def test_apply_retention_deletes_files_older_than_the_window(tmp_path):
    old_date = date(2026, 7, 20)
    _touch(tmp_path, f"{old_date.isoformat()}.db")
    today = old_date + timedelta(days=RETENTION_DAYS + 1)

    result = apply_retention(tmp_path, today)

    assert result.deleted == (tmp_path / f"{old_date.isoformat()}.db",)
    assert not (tmp_path / f"{old_date.isoformat()}.db").exists()


def test_apply_retention_keeps_files_at_exactly_the_boundary(tmp_path):
    boundary_date = date(2026, 7, 20)
    _touch(tmp_path, f"{boundary_date.isoformat()}.db")
    today = boundary_date + timedelta(days=RETENTION_DAYS)  # exactly 60 days old

    result = apply_retention(tmp_path, today)

    assert result.deleted == ()
    assert (tmp_path / f"{boundary_date.isoformat()}.db").exists()


def test_apply_retention_ignores_non_partition_files(tmp_path):
    _touch(tmp_path, "not-a-partition.db")
    today = date(2026, 9, 20)

    result = apply_retention(tmp_path, today)

    assert result.deleted == ()
    assert (tmp_path / "not-a-partition.db").exists()


def test_apply_retention_skips_deletion_when_not_yet_backed_up(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    history_dir.mkdir()
    backup_dir.mkdir()
    old_date = date(2026, 7, 20)
    _touch(history_dir, f"{old_date.isoformat()}.db")
    today = old_date + timedelta(days=RETENTION_DAYS + 1)

    result = apply_retention(history_dir, today, require_present_in=backup_dir)

    assert result.deleted == ()
    assert result.skipped_not_backed_up == (history_dir / f"{old_date.isoformat()}.db",)
    assert (history_dir / f"{old_date.isoformat()}.db").exists()


def test_apply_retention_deletes_once_present_in_backup(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    history_dir.mkdir()
    backup_dir.mkdir()
    old_date = date(2026, 7, 20)
    _touch(history_dir, f"{old_date.isoformat()}.db")
    _touch(backup_dir, f"{old_date.isoformat()}.db")
    today = old_date + timedelta(days=RETENTION_DAYS + 1)

    result = apply_retention(history_dir, today, require_present_in=backup_dir)

    assert result.deleted == (history_dir / f"{old_date.isoformat()}.db",)
    assert result.skipped_not_backed_up == ()

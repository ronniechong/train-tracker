import json
from datetime import date, datetime, timezone

from traintracker.archive.__main__ import _write_public_status_safely

NOW = datetime(2026, 8, 14, 3, 30, 0, tzinfo=timezone.utc)


def test_writes_status_normally(tmp_path):
    path = tmp_path / "public_status.json"
    _write_public_status_safely(path, date(2026, 8, 13), NOW)

    assert json.loads(path.read_text())["last_archived_date"] == "2026-08-13"


def test_swallows_oserror_instead_of_raising(tmp_path):
    # A path whose parent is a file (not a directory) makes the write's
    # own `mkdir(parents=True)` raise NotADirectoryError, an OSError
    # subclass -- exercises the real failure mode without needing to
    # mock the filesystem.
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("")
    path = blocking_file / "public_status.json"

    _write_public_status_safely(path, date(2026, 8, 13), NOW)  # must not raise

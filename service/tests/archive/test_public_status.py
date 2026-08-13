import json
from datetime import date, datetime, timezone

from traintracker.archive.public_status import write_public_status

NOW = datetime(2026, 8, 14, 3, 30, 0, tzinfo=timezone.utc)


def test_writes_latest_archived_date(tmp_path):
    path = tmp_path / "state" / "public_status.json"
    write_public_status(path, date(2026, 8, 13), NOW)

    body = json.loads(path.read_text())
    assert body["last_archived_date"] == "2026-08-13"
    assert body["updated_at"] == NOW.isoformat()


def test_writes_null_when_nothing_archived_yet(tmp_path):
    path = tmp_path / "public_status.json"
    write_public_status(path, None, NOW)

    body = json.loads(path.read_text())
    assert body["last_archived_date"] is None


def test_overwrites_atomically(tmp_path):
    path = tmp_path / "public_status.json"
    write_public_status(path, date(2026, 8, 12), NOW)
    write_public_status(path, date(2026, 8, 13), NOW)

    body = json.loads(path.read_text())
    assert body["last_archived_date"] == "2026-08-13"
    assert not path.with_suffix(".json.tmp").exists()

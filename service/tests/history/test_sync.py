import sqlite3
from datetime import date, datetime, timezone

from traintracker.history.store import HistoryStore
from traintracker.history.sync import sync_closed_partitions
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10):
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


def _write_partition(history_dir, service_date_at, event_trip_id):
    store = HistoryStore(history_dir)
    store.rotate(service_date_at)
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id=event_trip_id, observed_at=service_date_at,
            discrepancy_type="vp_without_tu", tu_value=None, vp_value="2",
        )
    )
    store.close()


def test_sync_copies_closed_partitions_and_leaves_the_original(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 7, 20), "t1")

    well_after_closed = datetime(2026, 7, 22, 0, 0, 0, tzinfo=timezone.utc)
    result = sync_closed_partitions(history_dir, backup_dir, well_after_closed)

    dest = backup_dir / "2026-07-20.db"
    assert result.synced == (dest,)
    assert dest.exists()
    assert (history_dir / "2026-07-20.db").exists()  # copy, not move


def test_sync_skips_a_partition_that_is_still_open(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 7, 20), "t1")

    still_open = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    result = sync_closed_partitions(history_dir, backup_dir, still_open)

    assert result.synced == ()
    assert not (backup_dir / "2026-07-20.db").exists()


def test_sync_is_idempotent_and_skips_already_synced_files(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 7, 20), "t1")
    well_after_closed = datetime(2026, 7, 22, 0, 0, 0, tzinfo=timezone.utc)

    first = sync_closed_partitions(history_dir, backup_dir, well_after_closed)
    second = sync_closed_partitions(history_dir, backup_dir, well_after_closed)

    assert len(first.synced) == 1
    assert second.synced == ()  # already present in backup_dir, no re-copy


def test_restore_from_backup_reads_back_the_same_rows(tmp_path):
    """The literal 'restore test' milestone 2e's AC asks for: back up a
    closed partition, simulate losing/deleting the original, and confirm the
    backup copy alone is a fully readable, correct SQLite file."""
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 7, 20), "the-real-trip")

    well_after_closed = datetime(2026, 7, 22, 0, 0, 0, tzinfo=timezone.utc)
    sync_closed_partitions(history_dir, backup_dir, well_after_closed)

    (history_dir / "2026-07-20.db").unlink()  # simulate the original being lost

    restored = backup_dir / "2026-07-20.db"
    conn = sqlite3.connect(restored)
    trip_ids = [row[0] for row in conn.execute("SELECT trip_id FROM discrepancy_events")]
    meta_date = conn.execute("SELECT service_date FROM meta").fetchone()[0]
    conn.close()

    assert trip_ids == ["the-real-trip"]
    assert meta_date == "2026-07-20"

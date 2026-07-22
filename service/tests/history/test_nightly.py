from datetime import datetime, timedelta, timezone

from traintracker.history.nightly import run_nightly_maintenance
from traintracker.history.retention import RETENTION_DAYS
from traintracker.history.store import HistoryStore
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10):
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


def _write_partition(history_dir, service_date_at, trip_id):
    store = HistoryStore(history_dir)
    store.rotate(service_date_at)
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id=trip_id, observed_at=service_date_at,
            discrepancy_type="vp_without_tu", tu_value=None, vp_value="2",
        )
    )
    store.close()


def test_recent_closed_partition_is_synced_but_not_yet_retained(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 5, 1), "t1")  # well within 60 days of "today"

    result = run_nightly_maintenance(history_dir, backup_dir, now=_at(2026, 5, 3))

    assert len(result.sync.synced) == 1
    assert (backup_dir / "2026-05-01.db").exists()
    assert result.history_retention.deleted == ()
    assert (history_dir / "2026-05-01.db").exists()


def test_simulated_fast_forward_past_retention_syncs_then_deletes_in_one_pass(tmp_path):
    """The AC's 'simulated/fast-forwarded date test': real 60-day elapsed
    time can't be observed within a session, so this ages a partition by
    constructing it with an old service_date and passing a far-future `now`
    directly, rather than waiting. Sync runs before retention within a
    single call, so a never-before-synced but already->60-day-old partition
    is expected to be backed up and pruned from history_dir in this same
    pass -- and, since retention prunes backup_dir on the same rule, it
    disappears from there too."""
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 1, 1), "t1")
    far_future = _at(2026, 1, 1) + timedelta(days=RETENTION_DAYS + 5)

    result = run_nightly_maintenance(history_dir, backup_dir, now=far_future)

    assert result.history_retention.skipped_not_backed_up == ()
    assert result.history_retention.deleted == (history_dir / "2026-01-01.db",)
    assert result.backup_retention.deleted == (backup_dir / "2026-01-01.db",)
    assert not (history_dir / "2026-01-01.db").exists()
    assert not (backup_dir / "2026-01-01.db").exists()


def test_a_partition_synced_in_an_earlier_pass_is_retained_until_it_actually_ages(tmp_path):
    history_dir = tmp_path / "history"
    backup_dir = tmp_path / "backup"
    _write_partition(history_dir, _at(2026, 1, 1), "t1")

    just_closed = _at(2026, 1, 1) + timedelta(days=1)
    first = run_nightly_maintenance(history_dir, backup_dir, now=just_closed)
    assert len(first.sync.synced) == 1
    assert first.history_retention.deleted == ()

    still_within_window = _at(2026, 1, 1) + timedelta(days=RETENTION_DAYS - 1)
    second = run_nightly_maintenance(history_dir, backup_dir, now=still_within_window)
    assert second.sync.synced == ()  # already synced, nothing new to copy
    assert second.history_retention.deleted == ()
    assert (history_dir / "2026-01-01.db").exists()

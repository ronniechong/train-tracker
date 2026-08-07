from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from traintracker.archive.compact import compact_partition
from traintracker.archive.write import staged_path, write_staged_parquet
from traintracker.history.store import HistoryStore
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)


def test_staged_path_layout():
    path = staged_path(Path("/staging"), "ghost_events", date(2026, 8, 7))
    assert str(path) == "/staging/ghost_events/year=2026/month=08/2026-08-07.parquet"


def test_write_staged_parquet_skips_empty_tables(tmp_path):
    store = HistoryStore(tmp_path / "history")
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="3",
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 7, 20)))
    staging_dir = tmp_path / "staging"
    written, empty = write_staged_parquet(tables, staging_dir, date(2026, 7, 20))

    # only discrepancy_events has a row in this fixture -- the other four
    # tables are genuinely empty, so no zero-row Parquet file is written
    # for them (that shape is exactly what broke datasets.load_dataset(),
    # 2026-08-08 incident).
    assert set(written) == {"discrepancy_events"}
    assert set(empty) == {
        "ghost_events", "poll_gap_events",
        "trip_completion_events", "delay_observation_events",
    }

    for table_name, path in written.items():
        assert path.exists()
        assert path == staging_dir / table_name / "year=2026" / "month=07" / "2026-07-20.parquet"

    # non-empty table round-trips its row correctly.
    discrepancy_table = pq.read_table(written["discrepancy_events"])
    assert discrepancy_table.num_rows == 1
    assert discrepancy_table.to_pylist()[0]["trip_id"] == "t1"

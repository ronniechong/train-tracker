from datetime import date, datetime, timezone

from traintracker.archive.compact import compact_partition
from traintracker.archive.drift import detect_drift
from traintracker.history.store import HistoryStore
from traintracker.state.merge import DiscrepancyEvent


def _at(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)


def test_no_findings_for_known_values(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="vp_without_tu",
            tu_value=None, vp_value="3",
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 7, 20)))
    assert detect_drift(tables) == []


def test_unknown_value_is_reported_but_archiving_still_succeeds(tmp_path):
    store = HistoryStore(tmp_path)
    store.rotate(_at(2026, 7, 20))
    store.discrepancy_log.record(
        DiscrepancyEvent(
            trip_id="t1", observed_at=_at(2026, 7, 20), discrepancy_type="a_brand_new_type",
            tu_value=None, vp_value="3",
        )
    )
    store.close()

    tables = compact_partition(store.partition_path(date(2026, 7, 20)))
    findings = detect_drift(tables)

    assert len(findings) == 1
    assert findings[0].table == "discrepancy_events"
    assert findings[0].column == "discrepancy_type"
    assert findings[0].unknown_values == frozenset({"a_brand_new_type"})
    # the table itself still has the row -- drift never blocks archiving
    assert tables["discrepancy_events"].num_rows == 1

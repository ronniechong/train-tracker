from datetime import date

from traintracker.gtfs.pinning import PinManifest, compare_trip_ids


def test_pin_is_created_on_first_call(tmp_path, sample_snapshot):
    manifest = PinManifest(tmp_path / "pins.json")
    result = manifest.pin(date(2026, 7, 20), sample_snapshot)
    assert result.was_new is True
    assert result.pin.digest == sample_snapshot.digest
    assert result.pin.service_date == "2026-07-20"


def test_pin_is_idempotent_under_a_republish_race(tmp_path, sample_snapshot):
    manifest = PinManifest(tmp_path / "pins.json")
    first = manifest.pin(date(2026, 7, 20), sample_snapshot)

    # Simulate the portal republishing between the two calls: a
    # different-content "snapshot" (different digest) tries to claim the
    # same service_date. The first pin must win — no silent repin.
    class _FakeRepublishedSnapshot:
        digest = "some-other-digest-from-a-republish"

    second = manifest.pin(date(2026, 7, 20), _FakeRepublishedSnapshot())

    assert second.was_new is False
    assert second.pin.digest == first.pin.digest == sample_snapshot.digest


def test_pin_persists_across_manifest_instances(tmp_path, sample_snapshot):
    path = tmp_path / "pins.json"
    PinManifest(path).pin(date(2026, 7, 20), sample_snapshot)

    reloaded = PinManifest(path)
    existing = reloaded.get(date(2026, 7, 20))
    assert existing is not None
    assert existing.digest == sample_snapshot.digest


def test_pin_get_returns_none_when_unpinned(tmp_path):
    manifest = PinManifest(tmp_path / "pins.json")
    assert manifest.get(date(2026, 7, 20)) is None


def test_compare_trip_ids_matches_m1_shape():
    # Mirrors the M1 finding: whole-snapshot trip_ids churn heavily across
    # publishes (future-dated trips get regenerated ids), but trip_ids
    # scoped to an elapsed/current service_date stay ~100% stable.
    old_whole_snapshot = frozenset(f"future_trip_{i}" for i in range(100))
    new_whole_snapshot = frozenset(f"future_trip_{i}" for i in range(56, 156))
    whole_churn = compare_trip_ids(old_whole_snapshot, new_whole_snapshot)
    assert whole_churn.churn_pct == 56.0  # only ids 56-99 (44 of them) survived

    stable_ids = frozenset({"elapsed_trip_1", "elapsed_trip_2", "elapsed_trip_3"})
    scoped_churn = compare_trip_ids(stable_ids, stable_ids)
    assert scoped_churn.churn_pct == 0.0
    assert scoped_churn.stable_pct == 100.0


def test_compare_trip_ids_handles_empty_old_set():
    result = compare_trip_ids(frozenset(), frozenset({"a"}))
    assert result.stable_pct == 100.0
    assert result.churn_pct == 0.0

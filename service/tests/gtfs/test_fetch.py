from datetime import date

import httpx
import pytest

from traintracker.gtfs.fetch import (
    StaticUrlNotConfigured,
    refresh_and_pin,
    static_gtfs_url,
)


def test_static_gtfs_url_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TT_GTFS_STATIC_URL", raising=False)
    with pytest.raises(StaticUrlNotConfigured):
        static_gtfs_url()


def test_refresh_and_pin_is_idempotent(tmp_path, sample_static_zip_bytes, monkeypatch):
    def fake_get(url, timeout=30.0, follow_redirects=True):
        return httpx.Response(200, content=sample_static_zip_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    store_dir = tmp_path / "snapshots"
    manifest_path = tmp_path / "pins.json"

    first = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, url="https://example.invalid/gtfs.zip"
    )
    assert first.pin_result.was_new is True
    assert first.stored_path.exists()

    second = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, url="https://example.invalid/gtfs.zip"
    )
    assert second.pin_result.was_new is False
    assert second.snapshot_digest == first.snapshot_digest
    # Only one file stored for this digest, even though "fetched" twice.
    assert len(list(store_dir.glob("*.zip"))) == 1

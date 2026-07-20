import io
import zipfile
from datetime import date

import httpx
import pytest

from traintracker.gtfs.fetch import (
    DEFAULT_GTFS_STATIC_URL,
    extract_mode_zip,
    refresh_and_pin,
    static_gtfs_url,
)


def _wrap_as_outer_zip(inner_zip_bytes: bytes, mode: str = "2") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as outer:
        outer.writestr(f"{mode}/google_transit.zip", inner_zip_bytes)
        # A sibling mode, to make sure extraction picks the right one.
        outer.writestr("11/google_transit.zip", b"not-metro-train-data")
    return buf.getvalue()


def test_static_gtfs_url_defaults_to_confirmed_portal_url(monkeypatch):
    monkeypatch.delenv("TT_GTFS_STATIC_URL", raising=False)
    assert static_gtfs_url() == DEFAULT_GTFS_STATIC_URL


def test_static_gtfs_url_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("TT_GTFS_STATIC_URL", "https://example.invalid/override.zip")
    assert static_gtfs_url() == "https://example.invalid/override.zip"


def test_extract_mode_zip_picks_the_right_mode(sample_static_zip_bytes):
    outer = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")
    extracted = extract_mode_zip(outer, mode="2")
    assert extracted == sample_static_zip_bytes


def test_extract_mode_zip_raises_on_missing_mode(sample_static_zip_bytes):
    outer = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")
    with pytest.raises(KeyError):
        extract_mode_zip(outer, mode="99")


def test_refresh_and_pin_is_idempotent(tmp_path, sample_static_zip_bytes, monkeypatch):
    outer_zip_bytes = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")

    def fake_get(url, timeout=120.0, follow_redirects=True):
        return httpx.Response(200, content=outer_zip_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    store_dir = tmp_path / "snapshots"
    manifest_path = tmp_path / "pins.json"

    first = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, url="https://example.invalid/gtfs.zip"
    )
    assert first.pin_result.was_new is True
    assert first.stored_path.exists()
    # The stored file is the extracted mode-2 inner zip, not the outer archive.
    assert first.stored_path.read_bytes() == sample_static_zip_bytes

    second = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, url="https://example.invalid/gtfs.zip"
    )
    assert second.pin_result.was_new is False
    assert second.snapshot_digest == first.snapshot_digest
    # Only one file stored for this digest, even though "fetched" twice.
    assert len(list(store_dir.glob("*.zip"))) == 1

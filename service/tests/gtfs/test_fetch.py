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


class _FakeHttp:
    """Stands in for httpx.get/httpx.head, counting calls so tests can
    assert the 270MB GET was actually skipped when the ETag matches."""

    def __init__(self, outer_zip_bytes: bytes, etag: str):
        self.outer_zip_bytes = outer_zip_bytes
        self.etag = etag
        self.get_calls = 0
        self.head_calls = 0

    def get(self, url, timeout=120.0, follow_redirects=True):
        self.get_calls += 1
        return httpx.Response(
            200,
            content=self.outer_zip_bytes,
            headers={"etag": self.etag},
            request=httpx.Request("GET", url),
        )

    def head(self, url, timeout=15.0, follow_redirects=True):
        self.head_calls += 1
        return httpx.Response(
            200, headers={"etag": self.etag}, request=httpx.Request("HEAD", url)
        )


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


def test_refresh_and_pin_downloads_on_first_call(tmp_path, sample_static_zip_bytes, monkeypatch):
    outer_zip_bytes = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")
    fake = _FakeHttp(outer_zip_bytes, etag='"v1"')
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "head", fake.head)

    result = refresh_and_pin(
        date(2026, 7, 20),
        tmp_path / "snapshots",
        tmp_path / "pins.json",
        tmp_path / "fetch_cache.json",
        url="https://example.invalid/gtfs.zip",
    )

    assert result.downloaded is True
    assert result.pin_result.was_new is True
    assert result.stored_path.read_bytes() == sample_static_zip_bytes
    assert fake.get_calls == 1
    assert fake.head_calls == 1


def test_refresh_and_pin_skips_download_when_etag_unchanged(
    tmp_path, sample_static_zip_bytes, monkeypatch
):
    outer_zip_bytes = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")
    fake = _FakeHttp(outer_zip_bytes, etag='"v1"')
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "head", fake.head)

    store_dir = tmp_path / "snapshots"
    manifest_path = tmp_path / "pins.json"
    cache_path = tmp_path / "fetch_cache.json"

    day_one = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )
    assert day_one.downloaded is True

    # Next day: portal's ETag hasn't changed (weekly update cadence) — the
    # job must still pin a new service_date, but without re-downloading.
    day_two = refresh_and_pin(
        date(2026, 7, 21), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )

    assert day_two.downloaded is False
    assert day_two.pin_result.was_new is True  # new service_date, still pinned
    assert day_two.snapshot_digest == day_one.snapshot_digest
    assert fake.get_calls == 1  # still just the one real download
    assert fake.head_calls == 2  # checked both days


def test_refresh_and_pin_redownloads_when_etag_changes(
    tmp_path, sample_static_zip_bytes, monkeypatch
):
    other_snapshot_bytes = sample_static_zip_bytes  # content doesn't matter here
    outer_zip_bytes = _wrap_as_outer_zip(other_snapshot_bytes, mode="2")
    fake = _FakeHttp(outer_zip_bytes, etag='"v1"')
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "head", fake.head)

    store_dir = tmp_path / "snapshots"
    manifest_path = tmp_path / "pins.json"
    cache_path = tmp_path / "fetch_cache.json"

    refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )

    fake.etag = '"v2"'  # portal published a new version
    second = refresh_and_pin(
        date(2026, 7, 21), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )

    assert second.downloaded is True
    assert fake.get_calls == 2


def test_refresh_and_pin_is_idempotent_for_the_same_service_date(
    tmp_path, sample_static_zip_bytes, monkeypatch
):
    outer_zip_bytes = _wrap_as_outer_zip(sample_static_zip_bytes, mode="2")
    fake = _FakeHttp(outer_zip_bytes, etag='"v1"')
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "head", fake.head)

    store_dir = tmp_path / "snapshots"
    manifest_path = tmp_path / "pins.json"
    cache_path = tmp_path / "fetch_cache.json"

    first = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )
    second = refresh_and_pin(
        date(2026, 7, 20), store_dir, manifest_path, cache_path,
        url="https://example.invalid/gtfs.zip",
    )

    assert second.pin_result.was_new is False
    assert second.snapshot_digest == first.snapshot_digest
    assert len(list(store_dir.glob("*.zip"))) == 1

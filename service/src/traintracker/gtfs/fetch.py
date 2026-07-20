"""Nightly static GTFS fetch + pin job.

TODO(ronnie): the static GTFS download URL/mechanism was never actually
scripted during M1 — the two reference zips in `spike/` were downloaded
manually via the Vic open-data portal. `GTFS_STATIC_URL` below is a
placeholder; confirm the real portal endpoint (and whether it needs auth —
unlike the realtime feeds, the static bulk download is likely unauthenticated
open data, but that's unverified) before this job runs for real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from .pinning import PinManifest, PinResult
from .snapshot import StaticSnapshot

GTFS_STATIC_URL_ENV = "TT_GTFS_STATIC_URL"


class StaticUrlNotConfigured(RuntimeError):
    pass


def static_gtfs_url() -> str:
    url = os.environ.get(GTFS_STATIC_URL_ENV)
    if not url:
        raise StaticUrlNotConfigured(
            f"{GTFS_STATIC_URL_ENV} is not set — the real static GTFS portal "
            "endpoint has not been confirmed yet (see module docstring)."
        )
    return url


def download_static_zip(url: str, timeout: float = 30.0) -> bytes:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


@dataclass(frozen=True)
class RefreshResult:
    snapshot_digest: str
    pin_result: PinResult
    stored_path: Path


def store_snapshot(raw_zip: bytes, digest: str, store_dir: Path) -> Path:
    """Save the raw zip under its content digest, so re-downloading
    unchanged content across nights never duplicates storage."""
    store_dir.mkdir(parents=True, exist_ok=True)
    dest = store_dir / f"{digest}.zip"
    if not dest.exists():
        dest.write_bytes(raw_zip)
    return dest


def refresh_and_pin(
    service_date: date,
    store_dir: Path,
    manifest_path: Path,
    url: str | None = None,
) -> RefreshResult:
    """The nightly job: fetch the current static feed, and pin it to
    `service_date` if that date has no pin yet (idempotent — a second call
    for the same service_date, whether from a re-run or a race with another
    nightly invocation, is a no-op that returns the original pin)."""
    raw = download_static_zip(url or static_gtfs_url())
    snapshot = StaticSnapshot.from_zip_bytes(raw)
    stored_path = store_snapshot(raw, snapshot.digest, store_dir)

    manifest = PinManifest(manifest_path)
    pin_result = manifest.pin(service_date, snapshot)

    return RefreshResult(
        snapshot_digest=snapshot.digest,
        pin_result=pin_result,
        stored_path=stored_path,
    )

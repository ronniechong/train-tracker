"""Nightly static GTFS fetch + pin job.

The Vic open-data portal publishes one zip-of-zips covering every transport
mode (~270MB): `https://opendata.transport.vic.gov.au/dataset/gtfs-schedule`
(confirmed by Ronnie 2026-07-20, resource
`fb152201-859f-4882-9206-b768060b50ad`). Inside it, each mode is a numbered
subdirectory containing its own `google_transit.zip` — mode `2` is Metro
Train (route_type 400, verified against `routes.txt`: Alamein/Belgrave/
Cranbourne etc. lines). We only ever need that ~20MB inner zip, not the full
outer archive, so it's extracted before parsing or storing — storing the
full 270MB nightly for 60 days of retention would be ~16GB vs. ~1.2GB for
just the Metro Train slice.

Side note (not this milestone's concern): the current live download includes
`shapes.txt`, which M1's captured reference snapshot did not — the earlier
"no shapes.txt, straight-line rendering" note in CLAUDE.md may be worth
revisiting at M4.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from .pinning import PinManifest, PinResult
from .snapshot import StaticSnapshot

GTFS_STATIC_URL_ENV = "TT_GTFS_STATIC_URL"

# Confirmed 2026-07-20 (see module docstring). Overridable via the env var
# above in case the portal ever changes the resource id.
DEFAULT_GTFS_STATIC_URL = (
    "https://opendata.transport.vic.gov.au/dataset/3f4e292e-7f8a-4ffe-831f-"
    "1953be0fe448/resource/fb152201-859f-4882-9206-b768060b50ad/download/"
    "gtfs.zip"
)

METRO_TRAIN_MODE = "2"


def static_gtfs_url() -> str:
    return os.environ.get(GTFS_STATIC_URL_ENV, DEFAULT_GTFS_STATIC_URL)


def download_static_zip(url: str, timeout: float = 120.0) -> bytes:
    """~270MB multi-modal outer zip — give it a real timeout, not the 30s
    default that's fine for the small per-mode inner zip."""
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


def extract_mode_zip(outer_zip_bytes: bytes, mode: str = METRO_TRAIN_MODE) -> bytes:
    """Pull one mode's `google_transit.zip` out of the multi-modal outer
    archive without touching the other modes' data."""
    entry = f"{mode}/google_transit.zip"
    with zipfile.ZipFile(io.BytesIO(outer_zip_bytes)) as outer:
        if entry not in outer.namelist():
            raise KeyError(
                f"{entry!r} not found in outer zip; portal layout may have "
                f"changed (saw: {outer.namelist()})"
            )
        return outer.read(entry)


@dataclass(frozen=True)
class RefreshResult:
    snapshot_digest: str
    pin_result: PinResult
    stored_path: Path


def store_snapshot(raw_zip: bytes, digest: str, store_dir: Path) -> Path:
    """Save the (already mode-extracted) zip under its content digest, so
    re-downloading unchanged content across nights never duplicates storage."""
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
    mode: str = METRO_TRAIN_MODE,
) -> RefreshResult:
    """The nightly job: fetch the current multi-modal static feed, extract
    the target mode's slice, and pin it to `service_date` if that date has
    no pin yet (idempotent — a second call for the same service_date,
    whether from a re-run or a race with another nightly invocation, is a
    no-op that returns the original pin)."""
    outer = download_static_zip(url or static_gtfs_url())
    inner = extract_mode_zip(outer, mode)
    snapshot = StaticSnapshot.from_zip_bytes(inner)
    stored_path = store_snapshot(inner, snapshot.digest, store_dir)

    manifest = PinManifest(manifest_path)
    pin_result = manifest.pin(service_date, snapshot)

    return RefreshResult(
        snapshot_digest=snapshot.digest,
        pin_result=pin_result,
        stored_path=stored_path,
    )

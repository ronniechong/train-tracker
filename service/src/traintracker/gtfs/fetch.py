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

Unlike the realtime feeds (M1 found no conditional-GET support at all), this
static endpoint sends real `ETag`/`Last-Modified` headers, and the site says
the content itself only changes weekly. A HEAD request costs ~0.25s vs. a
multi-second 270MB GET, so the job checks the ETag first and only downloads
when it's actually different from the last one seen — a service_date still
gets a pin every day (one row per service day is required regardless of
whether the underlying content changed), it just reuses the already-stored
digest on the ~6 days out of 7 where nothing changed.

Side note (not this milestone's concern): the current live download includes
`shapes.txt`, which M1's captured reference snapshot did not — the earlier
"no shapes.txt, straight-line rendering" note in CLAUDE.md may be worth
revisiting at M4.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from dataclasses import asdict, dataclass
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


def fetch_etag(url: str, timeout: float = 15.0) -> str | None:
    """Cheap HEAD request to check whether the remote content has changed,
    without pulling the 270MB body. Returns None if the server doesn't send
    an ETag at all (in which case the caller can't skip and must download)."""
    response = httpx.head(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.headers.get("etag")


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
class FetchCacheEntry:
    etag: str
    digest: str


class FetchCache:
    """Remembers the last ETag we saw and which digest it corresponds to,
    so the job can skip a 270MB re-download when nothing changed."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> FetchCacheEntry | None:
        if not self._path.exists():
            return None
        return FetchCacheEntry(**json.loads(self._path.read_text()))

    def save(self, entry: FetchCacheEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(entry), indent=2))


@dataclass(frozen=True)
class RefreshResult:
    snapshot_digest: str
    pin_result: PinResult
    stored_path: Path
    downloaded: bool  # False when skipped via a matching ETag


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
    cache_path: Path,
    url: str | None = None,
    mode: str = METRO_TRAIN_MODE,
) -> RefreshResult:
    """The nightly job: check whether the static feed actually changed
    (cheap HEAD + ETag compare), download + extract + parse only if it did,
    then pin the result to `service_date` if that date has no pin yet
    (idempotent — a second call for the same service_date, whether from a
    re-run or a race with another nightly invocation, is a no-op that
    returns the original pin)."""
    resolved_url = url or static_gtfs_url()
    cache = FetchCache(cache_path)
    cached = cache.load()

    remote_etag = fetch_etag(resolved_url)
    reused_path = store_dir / f"{cached.digest}.zip" if cached else None
    can_skip_download = (
        cached is not None
        and remote_etag is not None
        and remote_etag == cached.etag
        and reused_path.exists()
    )

    if can_skip_download:
        digest = cached.digest
        stored_path = reused_path
        downloaded = False
    else:
        outer = download_static_zip(resolved_url)
        inner = extract_mode_zip(outer, mode)
        snapshot = StaticSnapshot.from_zip_bytes(inner)
        digest = snapshot.digest
        stored_path = store_snapshot(inner, digest, store_dir)
        downloaded = True
        if remote_etag is not None:
            cache.save(FetchCacheEntry(etag=remote_etag, digest=digest))

    manifest = PinManifest(manifest_path)
    pin_result = manifest.pin_digest(service_date, digest)

    return RefreshResult(
        snapshot_digest=digest,
        pin_result=pin_result,
        stored_path=stored_path,
        downloaded=downloaded,
    )

#!/usr/bin/env python3
"""Pin a mode-1 (V/Line regional rail) static GTFS snapshot for the spike.

The published static GTFS dataset is a single ~250MB zip-of-zips covering
every transport mode. This script downloads it once, extracts ONLY the
``1/google_transit.zip`` member (V/Line regional trains — route_type 2 rail;
mode 5 is the separate V/Line coach product and is not extracted), and
deletes the outer archive immediately. The standing rule is that the full
outer archive is never kept on disk.

Unlike the realtime feeds, this endpoint honours conditional requests — the
script does a HEAD first and records the ETag / Last-Modified alongside the
snapshot so the capture window can be tied to an exact static version.

Usage::

    python pin_static.py --out-dir ../captures/vline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

DATASET_URL = (
    "https://opendata.transport.vic.gov.au/dataset/gtfs-schedule"
)
# The "GTFS Schedule" zip-of-zips (resource-id form the portal actually
# serves). Unauthenticated. Override via TT_GTFS_STATIC_URL if the portal
# moves the resource id.
DEFAULT_DOWNLOAD_URL = (
    "https://opendata.transport.vic.gov.au/dataset/3f4e292e-7f8a-4ffe-831f-"
    "1953be0fe448/resource/fb152201-859f-4882-9206-b768060b50ad/download/"
    "gtfs.zip"
)
DOWNLOAD_URL = os.environ.get("TT_GTFS_STATIC_URL", DEFAULT_DOWNLOAD_URL)

MODE_1_MEMBER = "1/google_transit.zip"


def _head() -> dict[str, str]:
    resp = requests.head(DOWNLOAD_URL, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return {
        k: resp.headers.get(k, "")
        for k in ("ETag", "Last-Modified", "Content-Length")
    }


def _download(dest: Path) -> None:
    with requests.get(DOWNLOAD_URL, timeout=120, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=Path("../captures/vline"))
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = out_dir / "mode1_snapshot.zip"
    manifest_path = out_dir / "mode1_snapshot_manifest.json"

    head = _head()
    print(f"remote: ETag={head['ETag']} Last-Modified={head['Last-Modified']} size={head['Content-Length']}")

    with tempfile.TemporaryDirectory() as tmp:
        outer = Path(tmp) / "gtfs_outer.zip"
        print(f"downloading outer archive to {outer} ...")
        _download(outer)
        print(f"downloaded {outer.stat().st_size} bytes")

        with zipfile.ZipFile(outer) as zf:
            members = zf.namelist()
            if MODE_1_MEMBER not in members:
                sys.exit(
                    f"{MODE_1_MEMBER} not found in outer archive. Members starting '1': "
                    + ", ".join(m for m in members if m.startswith("1"))
                )
            with zf.open(MODE_1_MEMBER) as src, snapshot_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        # TemporaryDirectory cleanup deletes the outer archive here.

    # Sanity: the extracted slice must be a readable zip with routes.txt.
    with zipfile.ZipFile(snapshot_path) as zf:
        names = zf.namelist()
        if "routes.txt" not in names:
            sys.exit(f"extracted {MODE_1_MEMBER} has no routes.txt — got {names}")

    manifest = {
        "pinned_at": datetime.now(timezone.utc).isoformat(),
        "source_url": DOWNLOAD_URL,
        "dataset_page": DATASET_URL,
        "mode": 1,
        "member": MODE_1_MEMBER,
        "outer_etag": head["ETag"],
        "outer_last_modified": head["Last-Modified"],
        "snapshot_sha256": _sha256(snapshot_path),
        "snapshot_bytes": snapshot_path.stat().st_size,
        "member_files": sorted(names),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"pinned {snapshot_path} ({manifest['snapshot_bytes']} bytes)")
    print(f"manifest {manifest_path}")


if __name__ == "__main__":
    main()

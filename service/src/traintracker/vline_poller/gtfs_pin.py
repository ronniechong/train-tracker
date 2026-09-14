"""`python -m traintracker.vline_poller.gtfs_pin` — V/Line's own nightly
static-GTFS fetch + per-service-day pin, mirroring `traintracker.gtfs`'s
job for Metro (same `refresh_and_pin`, mode `1` instead of `2`).

Intended for host cron via `docker compose run --rm --entrypoint
"python -m traintracker.vline_poller.gtfs_pin" vline-poller`.

`store_dir`/`manifest_path` point at V/Line's own `/data/gtfs` --
`vline_poller/__main__.py` reads from the same directory.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ..gtfs.fetch import VLINE_TRAIN_MODE, refresh_and_pin
from ..gtfs.gtfstime import service_date_for_instant
from ..redaction import configure_logging

logger = logging.getLogger("traintracker.vline_poller.gtfs_pin")

DATA_DIR = Path("/data")


def main() -> int:
    configure_logging(level=logging.INFO)

    gtfs_dir = DATA_DIR / "gtfs"
    service_date = service_date_for_instant(datetime.now(timezone.utc))

    result = refresh_and_pin(
        service_date=service_date,
        store_dir=gtfs_dir,
        manifest_path=gtfs_dir / "pin_manifest.json",
        cache_path=gtfs_dir / "fetch_cache.json",
        mode=VLINE_TRAIN_MODE,
    )
    logger.info(
        "vline gtfs refresh_and_pin: service_date=%s downloaded=%s digest=%s was_new_pin=%s",
        service_date,
        result.downloaded,
        result.snapshot_digest,
        result.pin_result.was_new,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

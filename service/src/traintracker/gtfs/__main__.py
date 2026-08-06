"""`python -m traintracker.gtfs` — runs the nightly static-GTFS fetch +
per-service-day pin once. Intended for host cron via `docker compose run
--rm --entrypoint "python -m traintracker.gtfs" poller` (same one-shot-
entrypoint pattern as `traintracker.history`'s `__main__.py`) — this module
builds and runs the job, it doesn't schedule itself. Real crontab wiring is
a separate ops step.

`store_dir`/`manifest_path` must point at the SAME `/data/gtfs` directory
`traintracker.poller`'s `PinnedScheduleCache` reads from (it looks up
snapshots as `{gtfs_dir}/{digest}.zip` and the pin manifest as
`{gtfs_dir}/pin_manifest.json`) -- this job writes into that directory
directly, not a separate "snapshots" subdirectory, so a freshly-run pin is
immediately visible to the already-running poller process without a
restart. `cache_path` (the ETag cache used to skip re-downloading unchanged
content) lives alongside them; nothing else reads it.

Resolves "today's" service_date the same way the rest of the app attributes
observed instants to a service day (`service_date_for_instant`, 3am local
boundary) -- this is why the cron entry must fire AFTER that boundary, not
before: run any earlier and this would silently pin *yesterday's*
already-pinned date instead (a harmless no-op, but one that leaves today's
real service_date unpinned until the next run).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ..redaction import configure_logging
from .fetch import refresh_and_pin
from .gtfstime import service_date_for_instant

logger = logging.getLogger("traintracker.gtfs")

DATA_DIR = Path("/data")


def main() -> int:
    # Routed through the shared redaction filter for consistency with
    # `poller`/`history` -- this job takes no secrets today, but
    # `configure_logging` also raises httpx's own logger to WARNING,
    # closing the same leak-vector class even though nothing here
    # currently triggers it.
    configure_logging(level=logging.INFO)

    gtfs_dir = DATA_DIR / "gtfs"
    service_date = service_date_for_instant(datetime.now(timezone.utc))

    result = refresh_and_pin(
        service_date=service_date,
        store_dir=gtfs_dir,
        manifest_path=gtfs_dir / "pin_manifest.json",
        cache_path=gtfs_dir / "fetch_cache.json",
    )
    logger.info(
        "gtfs refresh_and_pin: service_date=%s downloaded=%s digest=%s was_new_pin=%s",
        service_date,
        result.downloaded,
        result.snapshot_digest,
        result.pin_result.was_new,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`python -m traintracker.history` — run the nightly maintenance job once
(sync + retention). Intended for host cron via
`docker compose run --rm --entrypoint "python -m traintracker.history" poller`
(same one-shot-entrypoint pattern the Dockerfile already documents for
`traintracker.gateway`'s manual smoke check) — this module builds the
callable job only, it doesn't schedule itself. Real crontab wiring is a
separate ops step (same gap 2c's `refresh_and_pin` nightly job left open).

`/data` and `/backup` are fixed container-internal mount points (see
`Dockerfile`'s `VOLUME` declaration and `deploy/docker-compose.yml`) — same
pattern as the poller's existing `/data` mount: `TT_DATA_DIR`/`TT_BACKUP_DIR`
only ever exist as compose-level bind-mount substitutions on the host side,
never as environment variables inside the running container.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .nightly import run_nightly_maintenance

logger = logging.getLogger("traintracker.history")

DATA_DIR = Path("/data")
BACKUP_DIR = Path("/backup")


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    result = run_nightly_maintenance(
        history_dir=DATA_DIR / "history",
        backup_dir=BACKUP_DIR,
    )
    logger.info(
        "nightly maintenance: synced=%d history_deleted=%d history_skipped=%d backup_deleted=%d",
        len(result.sync.synced),
        len(result.history_retention.deleted),
        len(result.history_retention.skipped_not_backed_up),
        len(result.backup_retention.deleted),
    )
    if result.history_retention.skipped_not_backed_up:
        logger.warning(
            "retention skipped (not yet backed up): %s",
            [p.name for p in result.history_retention.skipped_not_backed_up],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

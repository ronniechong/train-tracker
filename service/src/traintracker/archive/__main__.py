"""`python -m traintracker.archive` -- run one archiver pass (compact any
not-yet-archived closed days, self-heal from backup where needed, upload to
the private Hugging Face dataset repo). Same one-shot-entrypoint pattern as
`traintracker.history`'s nightly job -- this module builds the callable
pass only, it doesn't schedule itself. This job runs in the SAME nightly
cron slot as the existing backup/retention job (not an independent
schedule), to shrink the window where a day's data exists in only one
failure domain before Hugging Face becomes a genuine offsite copy.

`/data`, `/backup` are the same fixed container-internal mount points
`traintracker.history` already uses, both READ-ONLY for this container
(the archiver never writes to data of record). `/staging` is this job's
own scratch directory for Parquet files about to be uploaded -- disposable,
re-derivable from the SQLite partition at any time. `/archive-state` is a
third, small, PERSISTENT+writable mount holding the gap/failure report
(`report.py`) and, as of the observability task, a node_exporter textfile-
collector metrics file (`metrics.py`) under `/archive-state/metrics/` --
same mount, since both are small persistent state this container needs
that must survive a restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..poller.healthcheck import ping
from ..redaction import configure_logging
from .metrics import write_textfile_metrics
from .report import prune_gap_report
from .run import run_archive_pass

logger = logging.getLogger("traintracker.archive")

DATA_DIR = Path("/data")
BACKUP_DIR = Path("/backup")
STAGING_DIR = Path("/staging")
ARCHIVE_STATE_DIR = Path("/archive-state")
METRICS_PATH = ARCHIVE_STATE_DIR / "metrics" / "archiver.prom"

HF_TOKEN_ENV = "HF_TOKEN"
HF_DATASET_REPO_ENV = "HF_DATASET_REPO"

# Own dedicated healthchecks.io check, deliberately separate from the
# poller's `TT_DEADMAN_PING_URL` -- this is a nightly batch job, not the
# realtime poll loop, and conflating the two checks would mean a single
# missed archiver run and a genuine poller outage look identical on the
# monitoring side.
ARCHIVE_DEADMAN_PING_URL_ENV = "TT_ARCHIVE_DEADMAN_PING_URL"


def main() -> int:
    token = os.environ.get(HF_TOKEN_ENV, "")
    repo_id = os.environ.get(HF_DATASET_REPO_ENV, "")

    # Redaction filter registered before anything else logs a single line --
    # same pattern as `gateway.__main__`'s `API_KEY_ENV` wiring, closing the
    # same leak-vector class this token would otherwise be exposed to.
    configure_logging(token, level=logging.INFO)

    if not token or not repo_id:
        logger.error(
            "%s and %s must both be set; refusing to run", HF_TOKEN_ENV, HF_DATASET_REPO_ENV
        )
        return 1

    now = datetime.now(timezone.utc)
    report_path = ARCHIVE_STATE_DIR / "archive_gap_report.jsonl"

    result = run_archive_pass(
        history_dir=DATA_DIR / "history",
        backup_dir=BACKUP_DIR,
        staging_dir=STAGING_DIR,
        report_path=report_path,
        repo_id=repo_id,
        token=token,
        now=now,
    )

    logger.info(
        "archive pass: archived=%d recovered_from_backup=%d failed=%d drift_findings=%d "
        "upload_retry_failures=%d",
        len(result.archived), len(result.recovered_from_backup),
        len(result.failed), len(result.drift_findings), result.upload_retry_failures,
    )
    if result.failed:
        logger.warning("unarchived days this pass: %s", [d.isoformat() for d in result.failed])

    pruned = prune_gap_report(report_path, now)
    if pruned:
        logger.info("pruned %d gap report entries older than 6 months", pruned)

    write_textfile_metrics(METRICS_PATH, result, now)

    # Pings on every completed pass, including ones that left some days
    # pending -- this check answers "did the archiver container itself run
    # to completion" (host/cron/image health), a distinct signal from data
    # backlog, which the days-pending/safety-net Grafana alerts cover.
    asyncio.run(_ping_deadman())

    return 0


async def _ping_deadman() -> None:
    async with httpx.AsyncClient() as client:
        await ping(client, os.environ.get(ARCHIVE_DEADMAN_PING_URL_ENV))


if __name__ == "__main__":
    raise SystemExit(main())

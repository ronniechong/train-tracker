"""`python -m traintracker.archive` -- run one archiver pass (compact any
not-yet-archived closed days, self-heal from backup where needed, upload to
the private Hugging Face dataset repo). Same one-shot-entrypoint pattern as
`traintracker.history`'s nightly job -- this module builds the callable
pass only, it doesn't schedule itself. Milestone 09's decision: this job
runs in the SAME nightly cron slot as the existing backup/retention job
(not an independent schedule), to shrink the window where a day's data
exists in only one failure domain before Hugging Face becomes a genuine
offsite copy.

`/data`, `/backup` are the same fixed container-internal mount points
`traintracker.history` already uses. `/staging` is this job's own scratch
directory for Parquet files about to be uploaded -- not one of the
project's existing bind mounts, since staged files are disposable
(re-derivable from the SQLite partition at any time) rather than data of
record.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..redaction import configure_logging
from .run import run_archive_pass

logger = logging.getLogger("traintracker.archive")

DATA_DIR = Path("/data")
BACKUP_DIR = Path("/backup")
STAGING_DIR = Path("/staging")

HF_TOKEN_ENV = "HF_TOKEN"
HF_DATASET_REPO_ENV = "HF_DATASET_REPO"


def main() -> int:
    token = os.environ.get(HF_TOKEN_ENV, "")
    repo_id = os.environ.get(HF_DATASET_REPO_ENV, "")

    # Redaction filter registered before anything else logs a single line --
    # same pattern as `gateway.__main__`'s `API_KEY_ENV` wiring, closing the
    # same leak-vector class (this token would otherwise be as exposed as
    # the PTV key was before that filter existed).
    configure_logging(token, level=logging.INFO)

    if not token or not repo_id:
        logger.error(
            "%s and %s must both be set; refusing to run", HF_TOKEN_ENV, HF_DATASET_REPO_ENV
        )
        return 1

    result = run_archive_pass(
        history_dir=DATA_DIR / "history",
        backup_dir=BACKUP_DIR,
        staging_dir=STAGING_DIR,
        report_path=DATA_DIR / "archive_gap_report.jsonl",
        repo_id=repo_id,
        token=token,
        now=datetime.now(timezone.utc),
    )

    logger.info(
        "archive pass: archived=%d recovered_from_backup=%d failed=%d drift_findings=%d",
        len(result.archived), len(result.recovered_from_backup),
        len(result.failed), len(result.drift_findings),
    )
    if result.failed:
        logger.warning("unarchived days this pass: %s", [d.isoformat() for d in result.failed])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

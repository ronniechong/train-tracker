"""Push staged Parquet files (`write.py`'s output) to the private Hugging
Face dataset repo, and answer "which days are already archived" for the
catch-up/self-healing orchestration in `run.py`.

Atomic commit: a single `upload_file` call per table per day -- sufficient
as long as the static-snapshot storage decision gate stays "inline per
day". If that gate later resolves to deduplicated storage, a day's archive
may need a multi-file commit; revisit this module then, not before.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from huggingface_hub import HfApi

from .schema import TABLE_SCHEMAS

logger = logging.getLogger("traintracker.archive.upload")

REPO_TYPE = "dataset"

_UPLOAD_ATTEMPTS = 3
_UPLOAD_BACKOFF_SECONDS = 5.0


@dataclass
class UploadStats:
    """Mutable counter threaded through a run: every failed attempt (not
    just a day's final exhausted failure) counts, so a flaky-but-eventually-
    successful upload still shows up in `archive_upload_errors_total` --
    the metric answers "how much retrying happened", not just "how many
    days are unarchived" (that's `archive_days_pending`, a separate signal)."""

    retry_failures: int = field(default=0)


def remote_path(table: str, service_date: date) -> str:
    """Repo-relative path -- same layout `write.staged_path` uses locally,
    just forward-slash joined (HF paths are always `/`, unlike `Path`)."""
    return (
        f"data/{table}/year={service_date.year:04d}"
        f"/month={service_date.month:02d}/{service_date.isoformat()}.parquet"
    )


def archived_days(repo_id: str, token: str) -> dict[str, set[date]]:
    """table -> set of service_dates already present in the repo, parsed
    from the repo's own file listing. One HTTP call for the whole repo,
    not per table/day -- cheap enough to call once per archiver run."""
    api = HfApi()
    files = api.list_repo_files(repo_id=repo_id, repo_type=REPO_TYPE, token=token)
    result: dict[str, set[date]] = defaultdict(set)
    for table in TABLE_SCHEMAS:
        prefix = f"data/{table}/"
        for path in files:
            if not path.startswith(prefix) or not path.endswith(".parquet"):
                continue
            stem = path.rsplit("/", 1)[-1].removesuffix(".parquet")
            try:
                result[table].add(date.fromisoformat(stem))
            except ValueError:
                continue  # not one of our own files; leave it alone
    return dict(result)


def is_day_fully_archived(archived: dict[str, set[date]], service_date: date) -> bool:
    return all(service_date in archived.get(table, set()) for table in TABLE_SCHEMAS)


def _upload_with_retry(
    api: HfApi,
    *,
    path: Path,
    path_in_repo: str,
    repo_id: str,
    token: str,
    commit_message: str,
    attempts: int = _UPLOAD_ATTEMPTS,
    backoff_seconds: float = _UPLOAD_BACKOFF_SECONDS,
    stats: UploadStats | None = None,
) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            api.upload_file(
                path_or_fileobj=path,
                path_in_repo=path_in_repo,
                repo_id=repo_id,
                repo_type=REPO_TYPE,
                token=token,
                commit_message=commit_message,
            )
            return
        except Exception as exc:  # noqa: BLE001 -- retried uniformly, re-raised if exhausted
            last_error = exc
            if stats is not None:
                stats.retry_failures += 1
            logger.warning(
                "upload attempt %d/%d failed for %s: %s", attempt + 1, attempts, path_in_repo, exc
            )
            if attempt < attempts - 1:
                time.sleep(backoff_seconds)
    assert last_error is not None
    raise last_error


def upload_day(
    repo_id: str,
    token: str,
    staged_paths: dict[str, Path],
    service_date: date,
    stats: UploadStats | None = None,
) -> None:
    """Upload every staged table file for one service day. Each table is
    its own `upload_file` commit (see module docstring) -- a partial
    failure partway through leaves some tables archived and others not,
    which is fine: `is_day_fully_archived` will correctly say "not yet"
    and the next run's catch-up pass retries only what's missing, per
    table, not the whole day over again."""
    api = HfApi()
    for table, path in staged_paths.items():
        _upload_with_retry(
            api,
            path=path,
            path_in_repo=remote_path(table, service_date),
            repo_id=repo_id,
            token=token,
            commit_message=f"archive {service_date.isoformat()}: {table}",
            stats=stats,
        )

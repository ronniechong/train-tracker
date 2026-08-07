"""Push staged Parquet files (`write.py`'s output) to the private Hugging
Face dataset repo, and answer "which days are already archived" for the
catch-up/self-healing orchestration in `run.py`.

Atomic commit: a single `upload_file` call per table per day -- sufficient
as long as the static-snapshot storage decision gate stays "inline per
day". If that gate later resolves to deduplicated storage, a day's archive
may need a multi-file commit; revisit this module then, not before.

`write.py` skips writing a Parquet file for a table with zero rows for the
day (a zero-row file is what broke `datasets.load_dataset()` in the
2026-08-08 incident). That means "does a file exist" can no longer answer
"was this table archived" on its own -- a genuinely empty table would
never get a file and `is_day_fully_archived` would say "not yet" forever,
making every archiver run re-upload the day's other tables in an endless
loop. `_empty_days_manifest_path`/`record_empty_day`/`archived_days`
close that gap with one small JSON manifest per table
(`data/<table>/_empty_days.json`, a sorted list of ISO dates) recording
"ran, genuinely zero rows" -- distinct from both "has a file" and
"never ran" (the latter stays covered by the existing staleness/gap
observability, unrelated to this manifest).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.errors import EntryNotFoundError

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


def _empty_days_manifest_path(table: str) -> str:
    return f"data/{table}/_empty_days.json"


def _load_empty_days(repo_id: str, token: str, table: str) -> set[date]:
    api = HfApi()
    try:
        local_path = api.hf_hub_download(
            repo_id=repo_id,
            repo_type=REPO_TYPE,
            token=token,
            filename=_empty_days_manifest_path(table),
        )
    except EntryNotFoundError:
        return set()
    with open(local_path) as f:
        raw = json.load(f)
    return {date.fromisoformat(d) for d in raw}


def record_empty_day(repo_id: str, token: str, table: str, service_date: date) -> None:
    """Mark `table` as genuinely zero-row for `service_date` -- called
    instead of uploading a Parquet file for it (see module docstring)."""
    existing = _load_empty_days(repo_id, token, table)
    existing.add(service_date)
    payload = json.dumps(sorted(d.isoformat() for d in existing), indent=2)
    api = HfApi()
    api.upload_file(
        path_or_fileobj=payload.encode(),
        path_in_repo=_empty_days_manifest_path(table),
        repo_id=repo_id,
        repo_type=REPO_TYPE,
        token=token,
        commit_message=f"archive {service_date.isoformat()}: {table} empty (0 rows)",
    )


def archived_days(repo_id: str, token: str) -> dict[str, set[date]]:
    """table -> set of service_dates already present in the repo, parsed
    from the repo's own file listing, unioned with each table's empty-days
    manifest (see module docstring). One file-listing HTTP call for the
    whole repo, plus one manifest download per table -- cheap enough to
    call once per archiver run."""
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
        result[table] |= _load_empty_days(repo_id, token, table)
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

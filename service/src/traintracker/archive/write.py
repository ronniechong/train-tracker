"""Write compacted pyarrow Tables (`compact.py`'s output) to local staging
Parquet files, in the layout the upload module will push to Hugging Face
verbatim: `data/<table>/year=YYYY/month=MM/YYYY-MM-DD.parquet`.

Deliberately a separate step from compaction (`compact_partition` never
touches disk) and from upload (`upload.py`, not built yet, never touches
SQLite) -- each module has exactly one I/O boundary, matching the
architecture principle that this whole pipeline is a chain of small,
independently-testable stages.

A table with zero rows for the day is NOT written -- a zero-row Parquet
file is exactly the shape that broke `datasets.load_dataset()` in
production (2026-08-08 incident: `ArrowInvalid: BatchSize must be greater
than 0`). "Does this day's file exist" is no longer the single check for
"was this table archived" -- `upload.record_empty_day`/`archived_days`
carry that signal instead, via a small per-table manifest, precisely so
this module doesn't have to fake a file to keep that check unambiguous.

Every written file is immediately reopened with `pq.ParquetFile` before
being reported back as staged -- catches a write that produced bytes on
disk but isn't actually readable, before it ever reaches Hugging Face.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def staged_path(staging_dir: Path, table: str, service_date: date) -> Path:
    return (
        staging_dir
        / table
        / f"year={service_date.year:04d}"
        / f"month={service_date.month:02d}"
        / f"{service_date.isoformat()}.parquet"
    )


def write_staged_parquet(
    tables: dict[str, pa.Table], staging_dir: Path, service_date: date
) -> tuple[dict[str, Path], tuple[str, ...]]:
    """Write one Parquet file per non-empty table to the staging layout.
    Returns (path written per table, names of tables skipped for having
    zero rows). Snappy compression (pyarrow's default) -- the broadly-
    compatible choice every major consumer (DuckDB, Spark, Athena/Glue,
    pandas) reads without extra configuration."""
    written: dict[str, Path] = {}
    empty: list[str] = []
    for table_name, table in tables.items():
        if table.num_rows == 0:
            empty.append(table_name)
            continue
        path = staged_path(staging_dir, table_name, service_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        pq.ParquetFile(path)  # fail loudly now, not after it reaches Hugging Face
        written[table_name] = path
    return written, tuple(empty)

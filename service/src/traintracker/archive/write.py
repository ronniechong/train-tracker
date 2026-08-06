"""Write compacted pyarrow Tables (`compact.py`'s output) to local staging
Parquet files, in the layout the upload module will push to Hugging Face
verbatim: `data/<table>/year=YYYY/month=MM/YYYY-MM-DD.parquet`.

Deliberately a separate step from compaction (`compact_partition` never
touches disk) and from upload (`upload.py`, not built yet, never touches
SQLite) -- each module has exactly one I/O boundary, matching the
architecture principle that this whole pipeline is a chain of small,
independently-testable stages.

Every table gets a file every closed day, even with zero rows -- so "does
this day's file exist" stays a single, unambiguous check for every
consumer of this output
(self-healing, catch-up diff, the 23-day safety net), rather than needing a
second rule for "was it legitimately empty."
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
) -> dict[str, Path]:
    """Write one Parquet file per table to the staging layout. Returns the
    path written for each table. Snappy compression (pyarrow's default) --
    the broadly-compatible choice every major consumer (DuckDB, Spark,
    Athena/Glue, pandas) reads without extra configuration."""
    written: dict[str, Path] = {}
    for table_name, table in tables.items():
        path = staged_path(staging_dir, table_name, service_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        written[table_name] = path
    return written

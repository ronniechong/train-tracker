"""Pre-deploy check: does every HistoryStore table write actually succeed
against a REAL, already-on-disk partition file, not just a freshly-created
one? `CREATE TABLE IF NOT EXISTS` is a no-op against a partition opened by
an older build, so unit tests -- which always start from an empty tmp_path
-- cannot catch a column added to `create_sql` without a matching
`migrations` entry (see history/store.py's `_TableSpec.migrations`). This
script is the thing that would have caught the 2026-08-09 ghost_events.reason
incident before it reached production.

Run before deploying ANY change to `history/store.py`'s table specs
(new column, new table, renamed column) against a copy of a real partition
file -- ideally today's live one, copied down read-only from wherever the
production host's history directory is (see ops docs for the actual path):

    scp <host>:<history-dir>/$(date +%F).db /tmp/preflight.db
    uv run python scripts/preflight_schema_check.py /tmp/preflight.db

Never point this at the live file directly -- it opens its own connection
and writes probe rows (rolled back before close), and the poller may have
it open for writes at the same time.

Exit code 0 = every table's insert_sql matches the file's real schema.
Non-zero = at least one table would crash the poller on write, printed
with the exact sqlite3 error.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from traintracker.history.store import _ALL_TABLES  # noqa: E402


def _probe_row(column_count: int) -> tuple:
    # Column types vary per table, but every declared column in this
    # codebase accepts NULL/0 for probe purposes except NOT NULL TEXT
    # columns, which SQLite is happy to receive an empty string for.
    return tuple("" for _ in range(column_count))


def check(db_path: Path) -> list[str]:
    failures: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        for spec in _ALL_TABLES:
            placeholder_count = spec.insert_sql.count("?")
            conn.execute("SAVEPOINT preflight")
            try:
                conn.execute(spec.insert_sql, _probe_row(placeholder_count))
            except sqlite3.OperationalError as exc:
                failures.append(f"{spec.name}: {exc}")
            finally:
                conn.execute("ROLLBACK TO preflight")
    finally:
        conn.close()
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src_path = Path(sys.argv[1])
    if not src_path.exists():
        print(f"error: {src_path} does not exist", file=sys.stderr)
        return 2

    # Work on a throwaway copy -- this script must never be the thing that
    # corrupts or locks a real partition file.
    with tempfile.TemporaryDirectory() as tmp:
        work_path = Path(tmp) / src_path.name
        shutil.copy2(src_path, work_path)

        failures = check(work_path)

    if failures:
        print(f"PREFLIGHT FAILED ({datetime.now(timezone.utc).isoformat()}) "
              f"against {src_path}:")
        for line in failures:
            print(f"  - {line}")
        print("\nEvery failure above WILL crash the poller in production the "
              "first time that event type fires. Add a `migrations` entry "
              "to the corresponding _TableSpec in history/store.py before "
              "deploying.")
        return 1

    print(f"PREFLIGHT OK against {src_path}: all {len(_ALL_TABLES)} tables "
          f"accept a write on the file's current on-disk schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

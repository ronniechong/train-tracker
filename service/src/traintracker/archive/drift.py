"""Free-text drift detection for the archive's open-string columns
(milestone 09's "Free-text drift detection" decision, 2026-08-07).

`discrepancy_type`, `reason`, and `status` stay open strings rather than
enums in the Parquet schema (matches live SQLite -- no CHECK constraints
there either, and no schema_version bump every time the code adds a new
value). In exchange, this module maintains a known-values allowlist per
column, seeded from every value the source code currently documents itself
as emitting (`state/merge.py`'s `DiscrepancyEvent.discrepancy_type` comment,
`poller/breaker.py`'s `PollGapEvent.reason` comment, `state/completion.py`'s
`Status` literal).

A value outside the allowlist NEVER blocks archiving -- the day still
compacts and uploads normally. `detect_drift` only returns findings for the
caller (the archiver's `__main__`) to log and alert on, so a new value gets
noticed and reviewed rather than silently drifting in unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

# (table, column) -> every value the source code currently documents itself
# as capable of emitting. Update this alongside the source comment it's
# seeded from whenever the code adds a genuinely new value on purpose --
# that keeps this an intentional edit, not silent schema drift.
KNOWN_VALUES: dict[tuple[str, str], frozenset[str]] = {
    ("discrepancy_events", "discrepancy_type"): frozenset(
        {
            "route_id_mismatch",
            "start_time_mismatch",
            "start_date_mismatch",
            "schedule_relationship_mismatch",
            "vp_without_tu",
        }
    ),
    ("poll_gap_events", "reason"): frozenset({"circuit_breaker"}),
    ("trip_completion_events", "status"): frozenset(
        {"on_time", "late", "cancelled", "undetermined_gap"}
    ),
}


@dataclass(frozen=True)
class DriftFinding:
    table: str
    column: str
    unknown_values: frozenset[str]


def detect_drift(tables: dict[str, pa.Table]) -> list[DriftFinding]:
    """Check every registered (table, column) pair against its allowlist.
    Gap-marker rows are naturally excluded -- their real-data columns are
    always null, and null values are dropped before comparison."""
    findings: list[DriftFinding] = []
    for (table, column), known in KNOWN_VALUES.items():
        pa_table = tables.get(table)
        if pa_table is None or pa_table.num_rows == 0:
            continue
        seen = set(pa_table.column(column).drop_null().unique().to_pylist())
        unknown = seen - known
        if unknown:
            findings.append(
                DriftFinding(table=table, column=column, unknown_values=frozenset(unknown))
            )
    return findings

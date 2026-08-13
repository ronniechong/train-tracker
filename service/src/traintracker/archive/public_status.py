"""A single public-safe fact about the archive pipeline -- the latest
service_date successfully archived to Hugging Face -- written where the
`poller`/API container can read it too.

Deliberately its own tiny file, separate from `report.py`'s gap report and
`metrics.py`'s Prometheus textfile: those two carry operational detail
(failure reasons, pending counts, table-level internals) that has no
public audience and stays archiver-only. This file carries the one fact
worth showing on the public site, and nothing else, so mounting
`/archive-state` read-only into `poller` can't leak more than that by
construction -- `poller`'s own code only ever opens this one path.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path


def write_public_status(path: Path, latest_archived_date: date | None, now: datetime) -> None:
    """Atomic write (temp + rename), same convention as `metrics.py`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "last_archived_date": latest_archived_date.isoformat() if latest_archived_date else None,
        "updated_at": now.isoformat(),
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body))
    os.replace(tmp_path, path)

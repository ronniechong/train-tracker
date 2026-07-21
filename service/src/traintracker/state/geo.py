"""Small geometry helpers shared by station-state derivation and the ghost
state machine. `CITY_LOOP_BBOX` mirrors the constant `spike/analyze.py` and
`spike/loop_gap_estimate.py` use - duplicated rather than imported because
`spike/` is a standalone M1 artifact with its own environment, not part of
this package. The 2d pre-analysis (`spike/FINDINGS.md` addendum,
2026-07-21) found loop-containment is ~0% of ghost-eligible gaps, so this
is a monitoring signal for the ghost event log, not an expected common case.
"""

from __future__ import annotations

import math

CITY_LOOP_BBOX = {
    "lat_min": -37.815,
    "lat_max": -37.808,
    "lon_min": 144.962,
    "lon_max": 144.975,
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_city_loop_bbox(lat: float, lon: float) -> bool:
    b = CITY_LOOP_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]

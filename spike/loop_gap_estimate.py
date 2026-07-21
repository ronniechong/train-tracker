#!/usr/bin/env python3
"""
2d pre-analysis — first estimate of the loop-gap share, run against the
existing M1 capture ahead of 2g's soak gate.

Extends M1's Q4 City Loop bbox (analyze.py) with a question Q4 didn't ask:
of the gaps long enough to trigger ghosting under the settled coasting
design (~60-90s coasting before -> ghost), what share have BOTH the
last-seen point AND the reappearance point inside the loop bbox? That
number is what CLAUDE.md's ghost-design revisit clause is keyed on: "if
~99% of ghost events are City Loop-contained -> dedicated 'in loop,
position unavailable' state instead of generic ghosting."

Standalone and dependency-light (no pandas) so it can run directly on
whatever host holds the raw capture, streaming one line at a time.

Usage:
    python3 loop_gap_estimate.py --vp-path data/vehicle_positions.ndjson
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime

# Same bbox M1's Q4 used (analyze.py) - loosely drawn around
# Flinders St -> Melbourne Central -> Parliament.
CITY_LOOP_BBOX = {
    "lat_min": -37.815,
    "lat_max": -37.808,
    "lon_min": 144.962,
    "lon_max": 144.975,
}

GHOST_THRESHOLDS_S = (60, 90)


def in_bbox(lat, lon) -> bool:
    b = CITY_LOOP_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


def stream_points(vp_path):
    by_trip = defaultdict(list)
    with open(vp_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["fetch_timestamp"])
            for e in rec.get("feed", {}).get("entity", []):
                v = e.get("vehicle", {})
                trip_id = v.get("trip", {}).get("trip_id")
                pos = v.get("position", {})
                lat, lon = pos.get("latitude"), pos.get("longitude")
                if trip_id is None or lat is None or lon is None:
                    continue
                by_trip[trip_id].append((ts, lat, lon))
    return by_trip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vp-path", required=True)
    args = parser.parse_args()

    by_trip = stream_points(args.vp_path)
    for points in by_trip.values():
        points.sort(key=lambda p: p[0])

    results = {}
    for threshold in GHOST_THRESHOLDS_S:
        total_gaps = 0
        loop_contained = 0  # both endpoints in bbox
        loop_entry = 0      # last-seen outside, reappears inside
        loop_exit = 0       # last-seen inside, reappears outside
        outside = 0         # neither endpoint in bbox

        for points in by_trip.values():
            for (t1, lat1, lon1), (t2, lat2, lon2) in zip(points, points[1:]):
                gap = (t2 - t1).total_seconds()
                if gap < threshold:
                    continue
                total_gaps += 1
                start_in, end_in = in_bbox(lat1, lon1), in_bbox(lat2, lon2)
                if start_in and end_in:
                    loop_contained += 1
                elif end_in and not start_in:
                    loop_entry += 1
                elif start_in and not end_in:
                    loop_exit += 1
                else:
                    outside += 1

        results[threshold] = {
            "total_ghost_eligible_gaps": total_gaps,
            "loop_contained": loop_contained,
            "loop_contained_pct": round(100 * loop_contained / total_gaps, 1) if total_gaps else None,
            "loop_entry": loop_entry,
            "loop_exit": loop_exit,
            "outside_network": outside,
        }

    print(json.dumps({
        "trips_seen": len(by_trip),
        "by_ghost_threshold_s": results,
    }, indent=2))


if __name__ == "__main__":
    main()

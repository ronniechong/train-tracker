#!/usr/bin/env python3
"""V/Line spike analyzer — turns a raw capture + pinned mode-1 static
snapshot into the numbers Phase A needs, written to a markdown report.

Answers, in order (see the milestone's Phase A checklist):

  1. Field population under sustained capture (does the single-snapshot
     "matches Metro" finding hold?)
  2. Coach (mode 5) contamination in the /v1/vline feed
  3. `-R` "Replacement Bus" convention in mode-1 routes.txt
  4. Same-service-day static join (route_id + trip_id)
  5. `distance_category` derivability (shape_dist_traveled / per-trip
     distance / stop-count fallback)
  6. Published short/long threshold validation (5:59 / 10:59) against
     observed terminus-arrival delay
  7. Coverage — scheduled vs observed trips, by time band
  8. Ghost / coasting — mid-journey disappearances

The decision calls (does 5:59/10:59 classify sensibly? do Metro's ghost
thresholds transfer?) are left for the human writing
`artifacts/vline-spike-findings.md` — this script only produces the
measurements.

Usage::

    python analyze_vline.py --capture-dir ../captures/vline \\
        --snapshot ../captures/vline/mode1_snapshot.zip --out report.md
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

MELB_TZ = ZoneInfo("Australia/Melbourne")

AM_PEAK = range(7, 10)
PM_PEAK = range(16, 19)
OVERNIGHT = range(1, 5)

# PTV publishes V/Line punctuality against a distance-based split. This is a
# best-effort line -> category map from the public convention; the analyzer
# reports per-route regardless so the findings author can re-slice it.
LONG_DISTANCE_HINTS = (
    "warrnambool", "albury", "shepparton", "swan hill", "echuca", "bairnsdale",
)
SHORT_LONG_THRESHOLDS_S = {"short": 359, "long": 659}  # 5:59 / 10:59


# --------------------------------------------------------------------------
# loading


def load_ndjson(path: Path):
    """Streams parsed records one at a time — a multi-day capture's full
    ndjson does not fit comfortably in memory alongside itself, so callers
    that need more than one pass re-invoke this rather than holding a list."""
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_static_table(zf: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in zf.namelist():
        return []
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig")
        return list(csv.DictReader(text))


# --------------------------------------------------------------------------
# feed iteration helpers


def vp_entities(records):
    for rec in records:
        ts = rec["fetch_timestamp"]
        for ent in rec.get("feed", {}).get("entity", []):
            vp = ent.get("vehicle")
            if vp:
                yield ts, vp


def tu_entities(records):
    for rec in records:
        ts = rec["fetch_timestamp"]
        for ent in rec.get("feed", {}).get("entity", []):
            tu = ent.get("trip_update")
            if tu:
                yield ts, tu


# --------------------------------------------------------------------------
# 1. field population


def analyze_field_population(vp_records) -> dict:
    total = 0
    present = Counter()
    for _ts, vp in vp_entities(vp_records):
        total += 1
        pos = vp.get("position", {})
        for field, ok in {
            "position.latitude": "latitude" in pos,
            "position.longitude": "longitude" in pos,
            "position.bearing": "bearing" in pos,
            "position.speed": "speed" in pos,
            "trip.trip_id": bool(vp.get("trip", {}).get("trip_id")),
            "trip.route_id": bool(vp.get("trip", {}).get("route_id")),
            "trip.direction_id": "direction_id" in vp.get("trip", {}),
            "trip.schedule_relationship": "schedule_relationship" in vp.get("trip", {}),
            "vehicle.id": bool(vp.get("vehicle", {}).get("id")),
            "current_status": "current_status" in vp,
            "stop_id": "stop_id" in vp,
            "occupancy_status": "occupancy_status" in vp,
        }.items():
            if ok:
                present[field] += 1
    return {
        "total_entities": total,
        "pct": {k: round(100 * present[k] / total, 1) for k in present} if total else {},
        "zero_fields": sorted(
            k for k in [
                "position.speed", "trip.direction_id", "trip.schedule_relationship",
                "current_status", "stop_id", "occupancy_status",
            ] if present[k] == 0
        ),
    }


# --------------------------------------------------------------------------
# 2 + 3 + 4. route/trip join, coach contamination, replacement bus


def analyze_routes(vp_records, tu_records, routes, trips) -> dict:
    route_type = {r["route_id"]: r.get("route_type", "") for r in routes}
    route_name = {
        r["route_id"]: (r.get("route_long_name") or r.get("route_short_name") or "")
        for r in routes
    }
    static_route_ids = set(route_type)
    static_trip_ids = {t["trip_id"] for t in trips}

    seen_route_ids = set()
    seen_trip_ids = set()
    for _ts, vp in vp_entities(vp_records):
        trip = vp.get("trip", {})
        if trip.get("route_id"):
            seen_route_ids.add(trip["route_id"])
        if trip.get("trip_id"):
            seen_trip_ids.add(trip["trip_id"])
    for _ts, tu in tu_entities(tu_records):
        trip = tu.get("trip", {})
        if trip.get("route_id"):
            seen_route_ids.add(trip["route_id"])
        if trip.get("trip_id"):
            seen_trip_ids.add(trip["trip_id"])

    route_join_misses = sorted(seen_route_ids - static_route_ids)
    trip_join_misses = seen_trip_ids - static_trip_ids

    non_rail = sorted(
        (rid, route_type.get(rid, "?"), route_name.get(rid, "?"))
        for rid in seen_route_ids & static_route_ids
        if route_type.get(rid) not in ("2", "")
    )

    replacement_bus = sorted(
        (r["route_id"], r.get("route_short_name", ""), r.get("route_long_name", ""))
        for r in routes
        if r["route_id"].endswith("-R")
        or (r.get("route_short_name", "").strip().lower() == "replacement bus")
    )

    return {
        "route_ids_seen": len(seen_route_ids),
        "route_id_join": f"{len(seen_route_ids) - len(route_join_misses)}/{len(seen_route_ids)}",
        "route_id_join_misses": route_join_misses,
        "trip_ids_seen": len(seen_trip_ids),
        "trip_id_join": f"{len(seen_trip_ids) - len(trip_join_misses)}/{len(seen_trip_ids)}",
        "trip_id_join_pct": round(
            100 * (len(seen_trip_ids) - len(trip_join_misses)) / len(seen_trip_ids), 1
        ) if seen_trip_ids else None,
        "non_rail_route_ids_in_feed": non_rail,
        "replacement_bus_routes": replacement_bus,
        "seen_route_ids": sorted(seen_route_ids),
        "route_type_histogram": dict(Counter(route_type.get(rid, "?") for rid in seen_route_ids)),
    }


# --------------------------------------------------------------------------
# 5. distance_category derivability


def analyze_distance_data(zf: zipfile.ZipFile, trips, routes) -> dict:
    stop_times = read_static_table(zf, "stop_times.txt")
    shapes = read_static_table(zf, "shapes.txt")

    st_has_dist = sum(1 for r in stop_times if r.get("shape_dist_traveled", "").strip())
    trips_with_shape = sum(1 for t in trips if t.get("shape_id", "").strip())

    # Per-trip route length proxy: max shape_dist_traveled across its stops.
    per_trip_max_dist: dict[str, float] = {}
    per_trip_stopcount: Counter = Counter()
    for r in stop_times:
        tid = r["trip_id"]
        per_trip_stopcount[tid] += 1
        val = r.get("shape_dist_traveled", "").strip()
        if val:
            try:
                per_trip_max_dist[tid] = max(per_trip_max_dist.get(tid, 0.0), float(val))
            except ValueError:
                pass

    route_of_trip = {t["trip_id"]: t.get("route_id", "") for t in trips}
    route_name = {r["route_id"]: (r.get("route_long_name") or "") for r in routes}

    # stop-count distribution per route (the fallback heuristic input)
    route_stopcounts: dict[str, list[int]] = defaultdict(list)
    for tid, n in per_trip_stopcount.items():
        route_stopcounts[route_of_trip.get(tid, "")].append(n)

    route_stop_summary = {
        route_name.get(rid, rid) or rid: {
            "trips": len(counts),
            "median_stops": statistics.median(counts) if counts else None,
            "min_stops": min(counts) if counts else None,
            "max_stops": max(counts) if counts else None,
        }
        for rid, counts in sorted(route_stopcounts.items())
    }

    return {
        "stop_times_rows": len(stop_times),
        "stop_times_with_shape_dist_traveled_pct": round(
            100 * st_has_dist / len(stop_times), 1
        ) if stop_times else 0.0,
        "trips_total": len(trips),
        "trips_with_shape_id_pct": round(100 * trips_with_shape / len(trips), 1) if trips else 0.0,
        "shapes_rows": len(shapes),
        "shapes_have_shape_dist_traveled": bool(shapes) and bool(
            shapes[0].get("shape_dist_traveled", "").strip()
        ),
        "trips_with_derivable_distance": len(per_trip_max_dist),
        "per_route_stopcount_summary": route_stop_summary,
    }


# --------------------------------------------------------------------------
# helpers for schedule-aware analyses


def parse_gtfs_time(value: str) -> timedelta:
    h, m, s = (int(x) for x in value.split(":"))
    return timedelta(hours=h, minutes=m, seconds=s)


def build_calendar(zf: zipfile.ZipFile) -> Callable[[date], set[str]]:
    """Returns resolve(service_date) -> set of active service_ids, memoized."""
    calendar = read_static_table(zf, "calendar.txt")
    cal_dates = read_static_table(zf, "calendar_dates.txt")
    weekday_cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def active_on(row: dict, d: date) -> bool:
        start = datetime.strptime(row["start_date"], "%Y%m%d").date()
        end = datetime.strptime(row["end_date"], "%Y%m%d").date()
        return start <= d <= end and row[weekday_cols[d.weekday()]] == "1"

    result: dict[date, set[str]] = {}

    def resolve(d: date) -> set[str]:
        if d in result:
            return result[d]
        sids = {row["service_id"] for row in calendar if active_on(row, d)}
        for row in cal_dates:
            if datetime.strptime(row["date"], "%Y%m%d").date() != d:
                continue
            if row["exception_type"] == "1":
                sids.add(row["service_id"])
            elif row["exception_type"] == "2":
                sids.discard(row["service_id"])
        result[d] = sids
        return sids

    return resolve


# --------------------------------------------------------------------------
# 6. threshold validation


def analyze_thresholds(tu_records, zf: zipfile.ZipFile, trips, routes) -> dict:
    stop_times = read_static_table(zf, "stop_times.txt")
    # last stop per trip by stop_sequence
    last_stop: dict[str, tuple[int, str, str]] = {}
    for r in stop_times:
        tid = r["trip_id"]
        seq = int(r["stop_sequence"])
        if tid not in last_stop or seq > last_stop[tid][0]:
            last_stop[tid] = (seq, r["stop_id"], r.get("arrival_time", ""))

    route_of_trip = {t["trip_id"]: t.get("route_id", "") for t in trips}
    route_name = {r["route_id"]: (r.get("route_long_name") or "") for r in routes}

    # For each trip: the terminus arrival_delay observed via the "arrival
    # present, departure absent" rule at the scheduled last stop.
    terminus_delay: dict[str, int] = {}
    for _ts, tu in tu_entities(tu_records):
        tid = tu.get("trip", {}).get("trip_id")
        if not tid or tid not in last_stop:
            continue
        term_stop = last_stop[tid][1]
        for stu in tu.get("stop_time_update", []):
            if stu.get("stop_id") != term_stop:
                continue
            arr = stu.get("arrival") or {}
            dep = stu.get("departure") or {}
            if "time" in arr and "time" not in dep:
                if "delay" in arr:
                    terminus_delay[tid] = int(arr["delay"])
                elif last_stop[tid][2]:
                    # fall back to observed vs scheduled if delay absent
                    pass

    by_category: dict[str, list[int]] = {"short": [], "long": []}
    per_route: dict[str, list[int]] = defaultdict(list)
    for tid, delay in terminus_delay.items():
        rid = route_of_trip.get(tid, "")
        name = route_name.get(rid, rid).lower()
        cat = "long" if any(h in name for h in LONG_DISTANCE_HINTS) else "short"
        by_category[cat].append(delay)
        per_route[route_name.get(rid, rid) or rid].append(delay)

    def summarize(delays: list[int], threshold_s: int) -> dict:
        if not delays:
            return {"n": 0}
        delays_sorted = sorted(delays)
        return {
            "n": len(delays),
            "median_s": statistics.median(delays),
            "p90_s": delays_sorted[int(0.9 * (len(delays) - 1))],
            "pct_within_threshold": round(
                100 * sum(1 for d in delays if d <= threshold_s) / len(delays), 1
            ),
            "threshold_s": threshold_s,
        }

    return {
        "trips_with_terminus_delay": len(terminus_delay),
        "short_distance": summarize(by_category["short"], SHORT_LONG_THRESHOLDS_S["short"]),
        "long_distance": summarize(by_category["long"], SHORT_LONG_THRESHOLDS_S["long"]),
        "per_route": {
            name: summarize(delays, SHORT_LONG_THRESHOLDS_S["short"])
            for name, delays in sorted(per_route.items())
        },
        "note": (
            "short/long split is a best-effort name match — reclassify from "
            "per_route + the distance analysis before trusting the aggregate"
        ),
    }


# --------------------------------------------------------------------------
# 7. coverage


def analyze_coverage(vp_records, tu_records, zf: zipfile.ZipFile, trips) -> dict:
    resolver = build_calendar(zf)
    trips_by_service: dict[str, list[str]] = defaultdict(list)
    for t in trips:
        trips_by_service[t.get("service_id", "")].append(t["trip_id"])

    dates_seen: set[date] = set()
    seen: set[str] = set()
    seen_hour: dict[int, set[str]] = defaultdict(set)

    def scan(records, sub_key: str) -> None:
        for rec in records:
            dt = datetime.fromisoformat(rec["fetch_timestamp"]).astimezone(MELB_TZ)
            dates_seen.add(dt.date())
            for ent in rec.get("feed", {}).get("entity", []):
                sub = ent.get(sub_key)
                tid = sub.get("trip", {}).get("trip_id") if sub else None
                if not tid:
                    continue
                seen.add(tid)
                seen_hour[dt.hour].add(tid)

    scan(vp_records, "vehicle")
    scan(tu_records, "trip_update")

    scheduled: set[str] = set()
    for d in dates_seen:
        for sid in resolver(d):
            scheduled.update(trips_by_service.get(sid, []))

    return {
        "capture_dates": sorted(d.isoformat() for d in dates_seen),
        "scheduled_trips": len(scheduled),
        "observed_trips": len(seen & scheduled),
        "coverage_pct": round(100 * len(seen & scheduled) / len(scheduled), 1) if scheduled else None,
        "observed_not_in_schedule": len(seen - scheduled),
        "observed_by_hour": {h: len(seen_hour[h]) for h in sorted(seen_hour)},
    }


# --------------------------------------------------------------------------
# 8. ghosting


def analyze_ghosting(vp_records, gap_threshold_s: int = 120) -> dict:
    timeline: dict[str, list[datetime]] = defaultdict(list)
    for ts, vp in vp_entities(vp_records):
        tid = vp.get("trip", {}).get("trip_id")
        if tid:
            timeline[tid].append(datetime.fromisoformat(ts))

    gap_durations: list[float] = []
    trips_with_gap = 0
    for tid, stamps in timeline.items():
        stamps.sort()
        had_gap = False
        for a, b in zip(stamps, stamps[1:]):
            delta = (b - a).total_seconds()
            if delta > gap_threshold_s:
                gap_durations.append(delta)
                had_gap = True
        if had_gap:
            trips_with_gap += 1

    return {
        "gap_threshold_s": gap_threshold_s,
        "trips_tracked": len(timeline),
        "trips_with_midjourney_gap": trips_with_gap,
        "gap_count": len(gap_durations),
        "gap_duration_median_s": round(statistics.median(gap_durations), 1) if gap_durations else None,
        "gap_duration_p90_s": round(sorted(gap_durations)[int(0.9 * (len(gap_durations) - 1))], 1)
        if gap_durations else None,
        "gap_duration_max_s": round(max(gap_durations), 1) if gap_durations else None,
    }


# --------------------------------------------------------------------------
# cadence recap (cheap, and lets the report stand alone)


def analyze_cadence(records) -> dict:
    etags = []
    fetches = 0
    for r in records:
        fetches += 1
        headers = r.get("response_headers")
        if headers:
            etags.append(headers.get("etag"))
    changes = sum(1 for a, b in zip(etags, etags[1:]) if a != b and a and b)
    return {
        "fetches": fetches,
        "etag_present_pct": round(100 * sum(1 for e in etags if e) / fetches, 1) if fetches else 0.0,
        "etag_changed_fraction": round(changes / fetches, 3) if fetches else None,
    }


# --------------------------------------------------------------------------
# report


def render(results: dict) -> str:
    return (
        "# V/Line spike — measurements\n\n"
        f"_Generated {datetime.now(timezone.utc).isoformat()} — feed the decision "
        "calls into `artifacts/vline-spike-findings.md`._\n\n"
        "```json\n" + json.dumps(results, indent=2, default=str) + "\n```\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capture-dir", type=Path, default=Path("../captures/vline"))
    parser.add_argument("--snapshot", type=Path, default=Path("../captures/vline/mode1_snapshot.zip"))
    parser.add_argument("--out", type=Path, default=Path("../captures/vline/report.md"))
    args = parser.parse_args()

    vp_path = args.capture_dir / "vehicle_positions.ndjson"
    tu_path = args.capture_dir / "trip_updates.ndjson"
    if not (vp_path.exists() and vp_path.stat().st_size) and not (
        tu_path.exists() and tu_path.stat().st_size
    ):
        raise SystemExit(f"no capture data under {args.capture_dir}")

    # Each call below re-reads its file from disk rather than sharing one
    # in-memory list — a multi-day capture parsed once already approaches
    # the host's RAM; parsed several times over (as the 8 analyses each
    # need their own pass) it doesn't fit. Re-reads hit the OS page cache.
    def vp_records():
        return load_ndjson(vp_path)

    def tu_records():
        return load_ndjson(tu_path)

    with zipfile.ZipFile(args.snapshot) as zf:
        routes = read_static_table(zf, "routes.txt")
        trips = read_static_table(zf, "trips.txt")
        cadence_recap = {
            "vp": analyze_cadence(vp_records()),
            "tu": analyze_cadence(tu_records()),
        }
        results = {
            "capture": {
                "vp_fetches": cadence_recap["vp"]["fetches"],
                "tu_fetches": cadence_recap["tu"]["fetches"],
            },
            "cadence_recap": cadence_recap,
            "1_field_population": analyze_field_population(vp_records()),
            "2_3_4_routes": analyze_routes(vp_records(), tu_records(), routes, trips),
            "5_distance_data": analyze_distance_data(zf, trips, routes),
            "6_thresholds": analyze_thresholds(tu_records(), zf, trips, routes),
            "7_coverage": analyze_coverage(vp_records(), tu_records(), zf, trips),
            "8_ghosting": analyze_ghosting(vp_records()),
        }

    args.out.write_text(render(results))
    print(f"wrote {args.out}")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()

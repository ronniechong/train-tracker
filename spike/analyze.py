#!/usr/bin/env python3
"""
M1 data spike — analyzer. Reads the ndjson capture files produced by
capture.py (and, optionally, a static GTFS zip) and regenerates FINDINGS.md.

Every number in sections 1-3 of FINDINGS.md is computed here, not
hand-written. Sections 4 (decision matrix "Measured"/"Resolution" columns)
and 5 (planning resolution) are left as placeholders for a human to fill in
after reading the numbers — re-running this script will NOT overwrite
hand-written sections if --preserve-manual is used against an existing file.

Usage:
    python analyze.py --data-dir data/ --static-gtfs gtfs_snapshot.zip --out FINDINGS.md
"""
import argparse
import bisect
import io
import json
import math
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

MELB_TZ = ZoneInfo("Australia/Melbourne")

# Approximate bounding box for the underground City Loop tunnel section
# between Flinders Street and Parliament (used for Q4 freeze/teleport
# detection). Loosely drawn around Flinders St -> Melbourne Central ->
# Parliament; refine once real capture data shows the actual GPS shadow.
CITY_LOOP_BBOX = {
    "lat_min": -37.815,
    "lat_max": -37.808,
    "lon_min": 144.962,
    "lon_max": 144.975,
}

# PTV route short names belonging to each City Loop running group. Matched
# against static GTFS routes.txt when available. Extend/correct once the
# static snapshot is in hand — these are best-effort from public PTV data.
LOOP_GROUPS = {
    "burnley": {"Belgrave", "Lilydale", "Alamein", "Glen Waverley"},
    "clifton_hill": {"Hurstbridge", "Mernda"},
    "northern": {"Craigieburn", "Upfield", "Sunbury", "Werribee", "Williamstown"},
}

AM_PEAK = (7, 9)
PM_PEAK = (16, 18)
OVERNIGHT = (1, 4)


def load_ndjson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def stream_ndjson(path: Path):
    """Yield one decoded record at a time instead of materializing the whole
    file. Full-scale captures (trip_updates.ndjson especially, with up to 31
    stop_time_update entries per trip) are large enough that loading every
    record into a persistent list exhausts RAM on a memory-constrained host —
    each record here is transient and GC'd once the caller extracts what it
    needs."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def load_light_records(path: Path) -> list[dict]:
    """Record-level fetch/header timestamps only, in the same shape fetch_dt()
    and header_ts() expect — safe to hold in memory in full even for
    multi-day captures since entity payloads (the actual bulk) are dropped."""
    out = []
    for rec in stream_ndjson(path):
        out.append({
            "fetch_timestamp": rec.get("fetch_timestamp"),
            "feed": {"header": {"timestamp": rec.get("feed", {}).get("header", {}).get("timestamp")}},
        })
    return out


def fetch_dt(record: dict) -> datetime:
    return datetime.fromisoformat(record["fetch_timestamp"])


def header_ts(record: dict) -> int | None:
    ts = record.get("feed", {}).get("header", {}).get("timestamp")
    return int(ts) if ts is not None else None


def entities(record: dict) -> list[dict]:
    return record.get("feed", {}).get("entity", [])


# ---------------------------------------------------------------------------
# Section 1: capture summary
# ---------------------------------------------------------------------------

def capture_summary(data_dir: Path, feed_records: dict[str, list[dict]], feed_errors: dict[str, list[dict]]) -> dict:
    all_ts = [fetch_dt(r) for recs in feed_records.values() for r in recs]
    window_start = min(all_ts) if all_ts else None
    window_end = max(all_ts) if all_ts else None
    hours_covered = (window_end - window_start).total_seconds() / 3600 if all_ts else 0

    peaks_covered = {}
    for feed, recs in feed_records.items():
        local_hours = {fetch_dt(r).astimezone(MELB_TZ).hour for r in recs}
        peaks_covered[feed] = {
            "am_peak": any(AM_PEAK[0] <= h < AM_PEAK[1] for h in local_hours),
            "pm_peak": any(PM_PEAK[0] <= h < PM_PEAK[1] for h in local_hours),
            "overnight": any(OVERNIGHT[0] <= h < OVERNIGHT[1] for h in local_hours),
        }

    error_counts = {}
    for feed, errs in feed_errors.items():
        error_counts[feed] = dict(Counter(e.get("error", "unknown") for e in errs))

    return {
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "hours_covered": round(hours_covered, 1),
        "polls_attempted": {f: len(feed_records[f]) + len(feed_errors[f]) for f in feed_records},
        "polls_succeeded": {f: len(feed_records[f]) for f in feed_records},
        "error_counts": error_counts,
        "peaks_covered": peaks_covered,
    }


# ---------------------------------------------------------------------------
# Q1: field population (Vehicle Positions)
# ---------------------------------------------------------------------------

Q1_FIELDS = {
    "position.latitude": lambda e: e.get("vehicle", {}).get("position", {}).get("latitude"),
    "position.longitude": lambda e: e.get("vehicle", {}).get("position", {}).get("longitude"),
    "position.bearing": lambda e: e.get("vehicle", {}).get("position", {}).get("bearing"),
    "position.speed": lambda e: e.get("vehicle", {}).get("position", {}).get("speed"),
    "stop_id": lambda e: e.get("vehicle", {}).get("stop_id"),
    "current_status": lambda e: e.get("vehicle", {}).get("current_status"),
    "occupancy_status": lambda e: e.get("vehicle", {}).get("occupancy_status"),
    "vehicle.id": lambda e: e.get("vehicle", {}).get("vehicle", {}).get("id"),
    "vehicle.label": lambda e: e.get("vehicle", {}).get("vehicle", {}).get("label"),
    "trip.trip_id": lambda e: e.get("vehicle", {}).get("trip", {}).get("trip_id"),
    "trip.route_id": lambda e: e.get("vehicle", {}).get("trip", {}).get("route_id"),
    "trip.direction_id": lambda e: e.get("vehicle", {}).get("trip", {}).get("direction_id"),
    "trip.schedule_relationship": lambda e: e.get("vehicle", {}).get("trip", {}).get("schedule_relationship"),
}


def q1_field_population(vp_path: Path) -> dict:
    counts = {field: 0 for field in Q1_FIELDS}
    total = 0
    for r in stream_ndjson(vp_path):
        for e in entities(r):
            if "vehicle" not in e:
                continue
            total += 1
            for field, getter in Q1_FIELDS.items():
                if getter(e) not in (None, ""):
                    counts[field] += 1
    if total == 0:
        return {"total_entities": 0, "fields": {}}
    return {"total_entities": total, "fields": {f: round(100 * c / total, 1) for f, c in counts.items()}}


# ---------------------------------------------------------------------------
# Q3: refresh cadence
# ---------------------------------------------------------------------------

def q3_cadence(records: list[dict]) -> dict:
    if not records:
        return {}
    rows = []
    for r in records:
        ht = header_ts(r)
        if ht is None:
            continue
        rows.append({"fetch": fetch_dt(r), "header_ts": ht})
    if not rows:
        return {}
    df = pd.DataFrame(rows).sort_values("fetch")

    # data age = fetch time - header timestamp
    df["age_s"] = df["fetch"].apply(lambda d: d.timestamp()) - df["header_ts"]

    # interval between *changes* in header timestamp (true feed refresh cadence)
    changed = df[df["header_ts"].ne(df["header_ts"].shift())]
    change_deltas = changed["header_ts"].diff().dropna()

    def pct(series, q):
        return round(float(series.quantile(q)), 2) if len(series) else None

    return {
        "n_polls": len(df),
        "n_header_changes": len(changed),
        "refresh_interval_s": {
            "p50": pct(change_deltas, 0.5),
            "p90": pct(change_deltas, 0.9),
            "min": round(float(change_deltas.min()), 2) if len(change_deltas) else None,
            "max": round(float(change_deltas.max()), 2) if len(change_deltas) else None,
        },
        "data_age_s": {
            "p50": pct(df["age_s"], 0.5),
            "p90": pct(df["age_s"], 0.9),
            "p99": pct(df["age_s"], 0.99),
            "max": round(float(df["age_s"].max()), 2),
        },
    }


def q3_rollover_correlation(vp_records: list[dict], tu_records: list[dict]) -> str | None:
    vp_changes = sorted({fetch_dt(r) for r in vp_records if header_ts(r) is not None})
    tu_changes = sorted({fetch_dt(r) for r in tu_records if header_ts(r) is not None})
    if not vp_changes or not tu_changes:
        return None
    vp_ts = pd.Series([d.timestamp() for d in vp_changes])
    tu_ts = pd.Series([d.timestamp() for d in tu_changes])
    # for each VP header-change instant, find nearest TU header-change instant
    diffs = [float((tu_ts - t).abs().min()) for t in vp_ts]
    within_one_interval = sum(1 for d in diffs if d <= 15) / len(diffs)
    return f"{round(100 * within_one_interval, 1)}% of VP header-changes have a TU header-change within 15s"


# ---------------------------------------------------------------------------
# Q4: City Loop behaviour
# ---------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def in_bbox(lat, lon) -> bool:
    b = CITY_LOOP_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


def q4_city_loop(vp_path: Path) -> dict:
    by_trip = defaultdict(list)
    for r in stream_ndjson(vp_path):
        ts = fetch_dt(r)
        for e in entities(r):
            v = e.get("vehicle", {})
            trip_id = v.get("trip", {}).get("trip_id")
            pos = v.get("position", {})
            lat, lon = pos.get("latitude"), pos.get("longitude")
            if trip_id is None or lat is None or lon is None:
                continue
            by_trip[trip_id].append((ts, lat, lon))

    freezes, disappearances, teleports = [], [], []
    for trip_id, points in by_trip.items():
        points.sort(key=lambda p: p[0])
        in_loop = [p for p in points if in_bbox(p[1], p[2])]
        if not in_loop:
            continue
        # freeze duration: consecutive fixes with identical lat/lon while in bbox
        run_start = None
        prev = None
        for ts, lat, lon in in_loop:
            if prev and (lat, lon) == (prev[1], prev[2]):
                if run_start is None:
                    run_start = prev[0]
            else:
                if run_start is not None:
                    freezes.append((prev[0] - run_start).total_seconds())
                run_start = None
            prev = (ts, lat, lon)
        if run_start is not None:
            freezes.append((prev[0] - run_start).total_seconds())

        # disappearance / teleport: gap between consecutive overall fixes for
        # this trip where the trip was last seen inside the bbox
        for (t1, lat1, lon1), (t2, lat2, lon2) in zip(points, points[1:]):
            gap = (t2 - t1).total_seconds()
            if gap <= 0:
                continue
            if in_bbox(lat1, lon1) and gap > 30:
                disappearances.append(gap)
                dist = haversine_m(lat1, lon1, lat2, lon2)
                if dist > 300:
                    teleports.append(dist)

    def summarize(vals):
        if not vals:
            return {"count": 0}
        s = pd.Series(vals)
        return {
            "count": len(vals),
            "p50": round(float(s.median()), 1),
            "max": round(float(s.max()), 1),
        }

    return {
        "trips_through_loop": len(
            {t for t, pts in by_trip.items() if any(in_bbox(p[1], p[2]) for p in pts)}
        ),
        "freeze_duration_s": summarize(freezes),
        "disappearance_gap_s": summarize(disappearances),
        "teleport_distance_m": summarize(teleports),
    }


# ---------------------------------------------------------------------------
# Q2: coverage percentage (needs static GTFS with calendar + stop_times)
# ---------------------------------------------------------------------------

WEEKDAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

Q2_BANDS = [
    ("overnight", 0, 7),
    ("am_peak", 7, 9),
    ("midday", 9, 16),
    ("pm_peak", 16, 18),
    ("evening", 18, 24),
]


def parse_gtfs_time(s: str) -> int:
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def q2_coverage(vp_path: Path, static: dict | None, sample_interval_min: int = 5) -> dict:
    if static is None or "stop_times" not in static:
        return {
            "computed": False,
            "note": "requires a static GTFS snapshot with calendar.txt, calendar_dates.txt and stop_times.txt",
        }

    trip_service = dict(zip(static["trips"]["trip_id"], static["trips"]["service_id"]))

    st = static["stop_times"]
    st = st.assign(arr_s=st["arrival_time"].map(parse_gtfs_time), dep_s=st["departure_time"].map(parse_gtfs_time))
    grouped = st.groupby("trip_id").agg(start_s=("arr_s", "min"), end_s=("dep_s", "max"))

    by_service: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for trip_id, row in grouped.iterrows():
        service_id = trip_service.get(trip_id)
        if service_id is None:
            continue
        by_service[service_id].append((trip_id, int(row["start_s"]), int(row["end_s"])))

    cal_rows = {}
    for _, row in static["calendar"].iterrows():
        cal_rows[row["service_id"]] = {
            "weekday": [row[d] == "1" for d in WEEKDAY_COLS],
            "start": datetime.strptime(row["start_date"], "%Y%m%d").date(),
            "end": datetime.strptime(row["end_date"], "%Y%m%d").date(),
        }
    cal_exceptions = defaultdict(dict)
    for _, row in static["calendar_dates"].iterrows():
        d = datetime.strptime(row["date"], "%Y%m%d").date()
        cal_exceptions[d][row["service_id"]] = row["exception_type"]

    def active_services(d) -> set[str]:
        active = {
            service_id for service_id, info in cal_rows.items()
            if info["start"] <= d <= info["end"] and info["weekday"][d.weekday()]
        }
        for service_id, exc in cal_exceptions.get(d, {}).items():
            if exc == "1":
                active.add(service_id)
            elif exc == "2":
                active.discard(service_id)
        return active

    active_cache: dict = {}

    def active_services_cached(d):
        if d not in active_cache:
            active_cache[d] = active_services(d)
        return active_cache[d]

    def scheduled_trip_ids(local_dt: datetime) -> set[str]:
        d = local_dt.date()
        secs = local_dt.hour * 3600 + local_dt.minute * 60 + local_dt.second
        result = set()
        for service_id in active_services_cached(d):
            for trip_id, s, e in by_service.get(service_id, []):
                if s <= secs <= e:
                    result.add(trip_id)
        # trips from the previous service-day that run past midnight (GTFS
        # represents these with times >= 24:00:00 rather than rolling the date)
        prev_d = d - timedelta(days=1)
        secs_prev = secs + 86400
        for service_id in active_services_cached(prev_d):
            for trip_id, s, e in by_service.get(service_id, []):
                if s >= 86400 and s <= secs_prev <= e:
                    result.add(trip_id)
        return result

    live_index = []
    for r in stream_ndjson(vp_path):
        ts = fetch_dt(r)
        ids = {
            e.get("vehicle", {}).get("trip", {}).get("trip_id")
            for e in entities(r)
        }
        ids.discard(None)
        live_index.append((ts, ids))
    live_index.sort(key=lambda p: p[0])

    if not live_index:
        return {"computed": False, "note": "no vehicle position polls available"}

    live_times = [p[0] for p in live_index]

    def nearest_live(t: datetime, max_gap_s: float = 60.0):
        i = bisect.bisect_left(live_times, t)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(live_times)]
        best = min(candidates, key=lambda idx: abs((live_times[idx] - t).total_seconds()))
        if abs((live_times[best] - t).total_seconds()) > max_gap_s:
            return None
        return live_index[best][1]

    window_start, window_end = live_times[0], live_times[-1]
    samples_by_band: dict[str, list[float]] = defaultdict(list)

    t = window_start
    step = timedelta(minutes=sample_interval_min)
    while t <= window_end:
        local = t.astimezone(MELB_TZ)
        band = next((name for name, lo, hi in Q2_BANDS if lo <= local.hour < hi), None)
        live_ids = nearest_live(t)
        if live_ids is not None and band is not None:
            sched_ids = scheduled_trip_ids(local)
            if sched_ids:
                pct = 100 * len(live_ids & sched_ids) / len(sched_ids)
                samples_by_band[band].append(pct)
        t += step

    bands_out = {}
    for name, _, _ in Q2_BANDS:
        vals = samples_by_band.get(name, [])
        if vals:
            s = pd.Series(vals)
            bands_out[name] = {
                "samples": len(vals),
                "mean_coverage_pct": round(float(s.mean()), 1),
                "p10_coverage_pct": round(float(s.quantile(0.1)), 1),
                "min_coverage_pct": round(float(s.min()), 1),
            }
        else:
            bands_out[name] = {"samples": 0}

    return {"computed": True, "sample_interval_min": sample_interval_min, "bands": bands_out}


# ---------------------------------------------------------------------------
# Q5: trip_id join integrity (needs static GTFS)
# ---------------------------------------------------------------------------

def load_static_gtfs(zip_path: Path | None) -> dict | None:
    if zip_path is None or not zip_path.exists():
        return None
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        trips = pd.read_csv(io.BytesIO(z.read("trips.txt")), dtype=str)
        routes = pd.read_csv(io.BytesIO(z.read("routes.txt")), dtype=str)
        result = {"trips": trips, "routes": routes}
        if {"calendar.txt", "calendar_dates.txt", "stop_times.txt"} <= names:
            result["calendar"] = pd.read_csv(io.BytesIO(z.read("calendar.txt")), dtype=str)
            result["calendar_dates"] = pd.read_csv(io.BytesIO(z.read("calendar_dates.txt")), dtype=str)
            result["stop_times"] = pd.read_csv(
                io.BytesIO(z.read("stop_times.txt")), dtype=str,
                usecols=["trip_id", "arrival_time", "departure_time"],
            )
    return result


def q5_join_integrity(vp_path: Path, tu_path: Path, static: dict | None) -> dict:
    # trip_id -> schedule_relationship (last value wins; a trip's relationship
    # shouldn't change mid-capture, but TU is the more authoritative source
    # since VP doesn't reliably populate schedule_relationship - see Q1)
    rt_trips: dict[str, str] = {}
    for r in stream_ndjson(vp_path):
        for e in entities(r):
            trip = e.get("vehicle", {}).get("trip", {})
            if trip.get("trip_id"):
                rt_trips[trip["trip_id"]] = trip.get("schedule_relationship", "SCHEDULED")
    for r in stream_ndjson(tu_path):
        for e in entities(r):
            trip = e.get("trip_update", {}).get("trip", {})
            if trip.get("trip_id"):
                rt_trips[trip["trip_id"]] = trip.get("schedule_relationship", "SCHEDULED")

    schedule_rel_counter = Counter(rt_trips.values())

    if static is None:
        return {
            "static_gtfs_available": False,
            "rt_trip_ids_seen": len(rt_trips),
            "schedule_relationship_counts": dict(schedule_rel_counter),
            "note": "no static GTFS snapshot provided; join rate not computed",
        }

    static_trip_ids = set(static["trips"]["trip_id"])

    def join_rate(ids: list[str]) -> float | None:
        if not ids:
            return None
        matched = sum(1 for i in ids if i in static_trip_ids)
        return round(100 * matched / len(ids), 1)

    all_ids = list(rt_trips.keys())
    scheduled_ids = [tid for tid, rel in rt_trips.items() if rel == "SCHEDULED"]
    non_scheduled_ids = [tid for tid, rel in rt_trips.items() if rel != "SCHEDULED"]
    unmatched = [tid for tid in all_ids if tid not in static_trip_ids]

    return {
        "static_gtfs_available": True,
        "rt_trip_ids_seen": len(all_ids),
        "join_pct_all": join_rate(all_ids),
        "join_pct_scheduled_only": join_rate(scheduled_ids),
        "note": (
            "the pre-committed 98% threshold should be judged against "
            "join_pct_scheduled_only - ADDED/DUPLICATED trips are "
            "real-time-only by GTFS-RT spec and will never appear in the "
            "static timetable, so they shouldn't count against the join rate"
        ),
        "unmatched_by_schedule_relationship": dict(
            Counter(rt_trips[tid] for tid in unmatched)
        ),
        "unmatched_scheduled_sample": [tid for tid in unmatched if rt_trips[tid] == "SCHEDULED"][:10],
        "schedule_relationship_counts": dict(schedule_rel_counter),
    }


# ---------------------------------------------------------------------------
# Q6: overnight / degraded states
# ---------------------------------------------------------------------------

def q6_overnight(vp_path: Path) -> dict:
    entity_counts = []
    zero_header_ts = set()
    for r in stream_ndjson(vp_path):
        hour = fetch_dt(r).astimezone(MELB_TZ).hour
        if not (OVERNIGHT[0] <= hour < OVERNIGHT[1]):
            continue
        c = len(entities(r))
        entity_counts.append(c)
        if c == 0:
            ht = header_ts(r)
            if ht is not None:
                zero_header_ts.add(ht)

    if not entity_counts:
        return {"overnight_polls": 0, "note": "no overnight window captured yet"}

    zero_entity_polls = sum(1 for c in entity_counts if c == 0)
    header_advances_at_zero = len(zero_header_ts) > 1 if zero_entity_polls >= 2 else None

    return {
        "overnight_polls": len(entity_counts),
        "zero_entity_polls": zero_entity_polls,
        "zero_entity_pct": round(100 * zero_entity_polls / len(entity_counts), 1),
        "header_timestamp_advances_with_zero_entities": header_advances_at_zero,
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

FINDINGS_TEMPLATE = """# M1 Data Spike — FINDINGS

_Generated by `analyze.py` on {generated_at}. Sections 1-3 are computed;
sections 4-5 require human input (Verdict lines, decision matrix
resolution, planning resolution)._

## 1. Capture summary

- Window: {window_start} -> {window_end} ({hours_covered}h covered)
- Peaks covered (per feed): {peaks_covered}
- Polls attempted / succeeded:
{polls_table}
- Error counts by type:
{error_table}

## 2. Findings per question

### Q1 — Field population (Vehicle Positions)
Entities analyzed: {q1_total}
{q1_table}
**Verdict:** _tbd_

### Q2 — Coverage percentage
{q2}
**Verdict:** _tbd_

### Q3 — Actual refresh cadence
Vehicle Positions:
{q3_vp}
Trip Updates:
{q3_tu}
Rollover correlation: {q3_rollover}
**Verdict:** _tbd_

### Q4 — City Loop behaviour
{q4}
**Verdict:** _tbd_

### Q5 — trip_id join integrity
{q5}
**Verdict:** _tbd_

### Q6 — Overnight and degraded states
{q6}
**Verdict:** _tbd_

## 3. Q7 probe results

_See `spike/probes.md` — run manually, not part of the capture loop._

## 4. Decision matrix

| Decision | Gate | Threshold (pre-committed) | Measured | Resolution |
|---|---|---|---|---|
| Station derivation in M2 | Q1 | `stop_id`+`current_status` >=95% populated -> optional; else mandatory | _tbd_ | _tbd_ |
| Ghost-train fallback in M4 | Q2 | coverage >=85% all bands -> skip; else build | _tbd_ | _tbd_ |
| Production poll interval | Q3 | set to ~1/3 of measured refresh interval, floor 10s | _tbd_ | _tbd_ |
| City Loop handling scope | Q4 | anomalies rare/short -> freeze-in-place; frequent/long -> dead-reckoning | _tbd_ | _tbd_ |
| trip_id reconciliation | Q5 | join >=98% -> direct join; else fuzzy/TU-based reconciliation in M2 | _tbd_ | _tbd_ |
| Staleness alert logic | Q6 | shaped entirely by observed overnight behaviour | _tbd_ | _tbd_ |
| Conditional GET in poller | Q7 | supported -> use; else plain GET + timestamp dedupe only | _tbd_ | _tbd_ |

## 5. Planning resolution

_Fill in by hand after reading sections 1-4:_

- Amendments to M2 scope:
- Amendments to M4 scope:
- New risks surfaced by the data:
- Assumptions in the master plan invalidated by this data:
- Deferred items resolved / re-deferred:
"""


def render_table(d: dict) -> str:
    if not d:
        return "  (no data)"
    return "\n".join(f"  - {k}: {v}" for k, v in d.items())


def render_nested(d: dict, indent: int = 0) -> str:
    lines = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}- {k}:")
            lines.append(render_nested(v, indent + 1))
        else:
            lines.append(f"{pad}- {k}: {v}")
    return "\n".join(lines)


def render_q2(q2: dict) -> str:
    if not q2.get("computed"):
        return f"  (not computed — {q2.get('note', 'unknown reason')})"
    lines = [f"  Sampled every {q2['sample_interval_min']}min against scheduled trips (static GTFS):"]
    for band, stats in q2["bands"].items():
        if stats["samples"] == 0:
            lines.append(f"  - {band}: (no samples)")
        else:
            lines.append(
                f"  - {band}: mean {stats['mean_coverage_pct']}%, "
                f"p10 {stats['p10_coverage_pct']}%, min {stats['min_coverage_pct']}% "
                f"(n={stats['samples']})"
            )
    return "\n".join(lines)


def render_q1_table(q1: dict) -> str:
    if q1["total_entities"] == 0:
        return "  (no vehicle position entities captured yet)"
    return "\n".join(f"  - {field}: {pct}%" for field, pct in q1["fields"].items())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--static-gtfs", type=Path, default=None, help="path to static GTFS zip snapshot")
    parser.add_argument("--out", default="FINDINGS.md", type=Path)
    args = parser.parse_args()

    feed_names = ["vehicle_positions", "trip_updates", "service_alerts"]
    paths = {f: args.data_dir / f"{f}.ndjson" for f in feed_names}
    light_records = {f: load_light_records(paths[f]) for f in feed_names}
    feed_errors = {f: load_ndjson(args.data_dir / f"{f}_errors.ndjson") for f in feed_names}

    summary = capture_summary(args.data_dir, light_records, feed_errors)
    q1 = q1_field_population(paths["vehicle_positions"])
    q3_vp = q3_cadence(light_records["vehicle_positions"])
    q3_tu = q3_cadence(light_records["trip_updates"])
    q3_rollover = q3_rollover_correlation(light_records["vehicle_positions"], light_records["trip_updates"])
    q4 = q4_city_loop(paths["vehicle_positions"])
    static = load_static_gtfs(args.static_gtfs)
    q2 = q2_coverage(paths["vehicle_positions"], static)
    q5 = q5_join_integrity(paths["vehicle_positions"], paths["trip_updates"], static)
    q6 = q6_overnight(paths["vehicle_positions"])

    report = FINDINGS_TEMPLATE.format(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_start=summary["window_start"],
        window_end=summary["window_end"],
        hours_covered=summary["hours_covered"],
        peaks_covered=summary["peaks_covered"],
        polls_table=render_table({
            f: f"{summary['polls_succeeded'][f]}/{summary['polls_attempted'][f]}" for f in feed_names
        }),
        error_table=render_table(summary["error_counts"]) or "  (none)",
        q1_total=q1["total_entities"],
        q1_table=render_q1_table(q1),
        q2=render_q2(q2),
        q3_vp=render_nested(q3_vp) if q3_vp else "  (no data)",
        q3_tu=render_nested(q3_tu) if q3_tu else "  (no data)",
        q3_rollover=q3_rollover or "  (insufficient data)",
        q4=render_nested(q4),
        q5=render_nested(q5),
        q6=render_nested(q6),
    )

    args.out.write_text(report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

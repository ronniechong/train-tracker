"""One-off build tool for the static geometry file ("Stage 2 — Static
geometry" in the map milestone). Not part of the running
service — reuses `traintracker.gtfs`'s static-GTFS parsing (fetch/extract/
stops) to derive a small station+route JSON consumed by the frontend, and
writes it straight into ../../web/src/data/geometry.json (crosses the
service/web boundary deliberately; this is a build-time tool, not runtime
code, so it doesn't go through the API).

Run: `uv run python scripts/build_web_geometry.py` from `service/`.

Deliberately excludes "Replacement Bus" services (route_id ending `-R:` in
this feed) -- these are real GTFS routes but not trains, and would be
misleading in a train map's line legend. Route stop sequences AND line
geometry both use the longest trip per route as a representative path --
same trip's `stop_times.txt` gives the station order, its `shape_id` gives
the real curved rail alignment from `shapes.txt` (2,251 distinct shape_ids
in this feed; only the ~18 actually needed are pulled out, not the whole
file). Falls back to straight lines between station coordinates only if a
route's representative trip has no shape_id or it's missing from
shapes.txt -- every route should get a real curve in practice.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from traintracker.gtfs.fetch import (  # noqa: E402
    METRO_TRAIN_MODE,
    VLINE_TRAIN_MODE,
    download_static_zip,
    extract_mode_zip,
    static_gtfs_url,
)

REPLACEMENT_BUS_SUFFIX = "-R:"
STATION_NAME_SUFFIX = " Railway Station"
BOUNDS_BUFFER_KM = 5.0
KM_PER_DEGREE_LAT = 111.0

# The live feed's `route_color` disagrees with PTV's own published metro
# line-color spec sheet for these five (checked against the official
# palette): Werribee/Williamstown come through pink
# (Sandringham's color) instead of green (Frankston's group), Sunbury comes
# through light blue (Cranbourne/Pakenham's group) instead of yellow
# (Craigieburn/Upfield's group), and Cranbourne/Pakenham are a close but
# not exact hex. Overriding with the spec sheet rather than trusting the
# feed for these route_short_names specifically.
ROUTE_COLOR_OVERRIDES = {
    "Werribee": "#028430",
    "Williamstown": "#028430",
    "Sunbury": "#FFBE00",
    "Cranbourne": "#279FD5",
    "Pakenham": "#279FD5",
}

# V/Line's brand colour. Every V/Line route already comes through the feed
# as one uniform color (`#8F1A95`), just not an exact match for the brand
# hex -- one unconditional override per route, not a per-line lookup.
VLINE_BRAND_COLOR = "#7F0D82"

# V/Line only renders at zoom 6-8, well below Metro's up-to-15 -- raw
# shapes.txt density is wasted there. ~35m tolerance, imperceptible at
# that zoom.
VLINE_SIMPLIFY_TOLERANCE_DEG = 0.0003


def _perpendicular_distance_deg(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    nx, ny = sx + t * dx, sy + t * dy
    return math.hypot(px - nx, py - ny)


def simplify_shape(points: list[list[float]], tolerance_deg: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker line simplification, in plain lon/lat degrees."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    max_dist = -1.0
    max_idx = 0
    for i in range(1, len(points) - 1):
        dist = _perpendicular_distance_deg(points[i], start, end)
        if dist > max_dist:
            max_dist = dist
            max_idx = i
    if max_dist > tolerance_deg:
        left = simplify_shape(points[: max_idx + 1], tolerance_deg)
        right = simplify_shape(points[max_idx:], tolerance_deg)
        return left[:-1] + right
    return [start, end]


def load_mode_zip(cache_path: Path, mode: str) -> bytes:
    if cache_path.exists():
        return cache_path.read_bytes()
    outer = download_static_zip(static_gtfs_url())
    inner = extract_mode_zip(outer, mode=mode)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(inner)
    return inner


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))


def _shape_points_for(zf: zipfile.ZipFile, target_shape_ids: set[str]) -> dict[str, list[tuple[int, float, float]]]:
    """Streams shapes.txt row-by-row rather than loading all ~1.37M rows via
    `_read_csv` -- only the handful of shape_ids actually needed (one per
    route) are kept, everything else is discarded as it's read."""
    points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    with zf.open("shapes.txt") as raw:
        wrapper = io.TextIOWrapper(raw, encoding="utf-8-sig")
        for row in csv.DictReader(wrapper):
            shape_id = row["shape_id"]
            if shape_id in target_shape_ids:
                points[shape_id].append(
                    (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
                )
    return points


def build_geometry(inner_zip_bytes: bytes, mode: str = METRO_TRAIN_MODE) -> dict:
    with zipfile.ZipFile(io.BytesIO(inner_zip_bytes)) as zf:
        stop_rows = _read_csv(zf, "stops.txt")
        route_rows = _read_csv(zf, "routes.txt")
        trip_rows = _read_csv(zf, "trips.txt")
        stop_time_rows = _read_csv(zf, "stop_times.txt")

        return _build_geometry_from_rows(zf, stop_rows, route_rows, trip_rows, stop_time_rows, mode)


def _build_geometry_from_rows(
    zf: zipfile.ZipFile,
    stop_rows: list[dict[str, str]],
    route_rows: list[dict[str, str]],
    trip_rows: list[dict[str, str]],
    stop_time_rows: list[dict[str, str]],
    mode: str = METRO_TRAIN_MODE,
) -> dict:
    stations: dict[str, dict] = {
        r["stop_id"]: {
            "id": r["stop_id"],
            "name": r["stop_name"].removesuffix(STATION_NAME_SUFFIX),
            "lat": float(r["stop_lat"]),
            "lon": float(r["stop_lon"]),
        }
        for r in stop_rows
        if r["location_type"] == "1"
    }

    platform_to_station = {
        r["stop_id"]: r["parent_station"]
        for r in stop_rows
        if r["location_type"] != "1" and r["parent_station"]
    }

    def route_color(r: dict[str, str]) -> str:
        if mode == VLINE_TRAIN_MODE:
            return VLINE_BRAND_COLOR
        return ROUTE_COLOR_OVERRIDES.get(
            r["route_short_name"],
            f"#{r['route_color']}" if r["route_color"] else "#888888",
        )

    routes: dict[str, dict] = {
        r["route_id"]: {
            "id": r["route_id"],
            "name": r["route_short_name"] or r["route_long_name"],
            "color": route_color(r),
        }
        for r in route_rows
        if not r["route_id"].endswith(REPLACEMENT_BUS_SUFFIX)
    }

    trip_route = {r["trip_id"]: r["route_id"] for r in trip_rows}
    trip_shape = {r["trip_id"]: r["shape_id"] for r in trip_rows if r["shape_id"]}

    trip_stop_seq: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in stop_time_rows:
        route_id = trip_route.get(r["trip_id"])
        if route_id in routes:
            trip_stop_seq[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))

    best_trip_for_route: dict[str, str] = {}
    for trip_id, seq in trip_stop_seq.items():
        route_id = trip_route[trip_id]
        current_best = best_trip_for_route.get(route_id)
        if current_best is None or len(seq) > len(trip_stop_seq[current_best]):
            best_trip_for_route[route_id] = trip_id

    target_shape_ids = {
        trip_shape[trip_id] for trip_id in best_trip_for_route.values() if trip_id in trip_shape
    }
    shape_points = _shape_points_for(zf, target_shape_ids)

    used_station_ids: set[str] = set()
    dangling_refs: set[str] = set()
    shape_fallback_routes: list[str] = []
    for route_id, route in routes.items():
        trip_id = best_trip_for_route.get(route_id)
        if trip_id is None:
            route["stationIds"] = []
            route["shape"] = []
            continue
        ordered_platforms = [stop_id for _, stop_id in sorted(trip_stop_seq[trip_id])]
        station_seq: list[str] = []
        for platform_id in ordered_platforms:
            station_id = platform_to_station.get(platform_id, platform_id)
            if not station_seq or station_seq[-1] != station_id:
                station_seq.append(station_id)
        route["stationIds"] = station_seq
        for station_id in station_seq:
            if station_id in stations:
                used_station_ids.add(station_id)
            else:
                dangling_refs.add(station_id)

        shape_id = trip_shape.get(trip_id)
        points = shape_points.get(shape_id) if shape_id else None
        if points:
            shape = [[lon, lat] for _, lat, lon in sorted(points)]
            if mode == VLINE_TRAIN_MODE:
                shape = simplify_shape(shape, VLINE_SIMPLIFY_TOLERANCE_DEG)
            route["shape"] = shape
        else:
            # Fallback: straight lines between station coordinates, same as
            # before shapes.txt was wired up -- only hit if a route's
            # representative trip genuinely has no usable shape.
            shape_fallback_routes.append(route_id)
            route["shape"] = [
                [stations[sid]["lon"], stations[sid]["lat"]] for sid in station_seq if sid in stations
            ]

    if shape_fallback_routes:
        print(
            f"NOTE: {len(shape_fallback_routes)} route(s) fell back to straight-line "
            f"geometry (no usable shape_id): {sorted(shape_fallback_routes)}",
            file=sys.stderr,
        )

    if dangling_refs:
        print(
            f"WARNING: {len(dangling_refs)} station id(s) referenced by a route's "
            f"stop sequence have no location_type=1 row in stops.txt: {sorted(dangling_refs)}",
            file=sys.stderr,
        )

    used_stations = {sid: stations[sid] for sid in used_station_ids}
    lats = [s["lat"] for s in used_stations.values()]
    lons = [s["lon"] for s in used_stations.values()]
    mean_lat = sum(lats) / len(lats)
    lat_buffer = BOUNDS_BUFFER_KM / KM_PER_DEGREE_LAT
    lon_buffer = BOUNDS_BUFFER_KM / (KM_PER_DEGREE_LAT * math.cos(math.radians(mean_lat)))

    return {
        "stations": sorted(used_stations.values(), key=lambda s: s["name"]),
        "routes": sorted(routes.values(), key=lambda r: r["name"]),
        "bounds": {
            "west": min(lons) - lon_buffer,
            "east": max(lons) + lon_buffer,
            "south": min(lats) - lat_buffer,
            "north": max(lats) + lat_buffer,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[METRO_TRAIN_MODE, VLINE_TRAIN_MODE],
        default=METRO_TRAIN_MODE,
        help="GTFS mode to build geometry for: metro train (2, default) or V/Line train (1).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Local cache of the extracted mode zip, to avoid re-downloading the ~270MB outer archive on every run. Defaults to a mode-specific path.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Defaults to web/src/data/geometry.json (metro) or vline-geometry.json (V/Line).",
    )
    args = parser.parse_args()

    is_vline = args.mode == VLINE_TRAIN_MODE
    cache = args.cache or Path(__file__).resolve().parent / ".gtfs_cache" / ("vline.zip" if is_vline else "metro.zip")
    out = args.out or Path(__file__).resolve().parents[2] / "web" / "src" / "data" / (
        "vline-geometry.json" if is_vline else "geometry.json"
    )

    inner = load_mode_zip(cache, args.mode)
    geometry = build_geometry(inner, args.mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(geometry, indent=2) + "\n")
    print(f"wrote {len(geometry['stations'])} stations, {len(geometry['routes'])} routes -> {out}")


if __name__ == "__main__":
    main()

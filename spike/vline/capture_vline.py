#!/usr/bin/env python3
"""V/Line GTFS-R data spike — raw capture for Vehicle Positions + Trip Updates.

Spike-grade and deliberately standalone: it does NOT import the production
service. It polls the V/Line VP and TU feeds on a fixed interval and appends
one newline-delimited JSON record per successful poll to
``<out-dir>/<feed>.ndjson`` (decoded FeedMessage plus fetch metadata).
Service Alerts is intentionally not polled — V/Line ships without it.

Hard stop: this process terminates itself after ``--max-runtime-hours``
(default 72). A previous V/Line capture was started with ``nohup ... &`` and
no stop and drifted to five unattended days; an unbounded run
(``--max-runtime-hours 0``) is refused here on purpose.

Usage (on the capture host)::

    export VIC_TRANSPORT_API_KEY=...
    python capture_vline.py --out-dir ../captures/vline --max-runtime-hours 72

Register a key at https://opendata.transport.vic.gov.au/ — it is sent as the
``KeyId`` header (NOT ``Ocp-Apim-Subscription-Key``; the published OpenAPI
docs are wrong about that).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google.protobuf.json_format import MessageToDict
from google.transit import gtfs_realtime_pb2

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is a convenience, not a requirement
    def load_dotenv() -> None:  # type: ignore[misc]
        return None

BASE_URL = (
    "https://api.opendata.transport.vic.gov.au/opendata/public-transport"
    "/gtfs/realtime/v1/vline"
)

FEEDS = {
    "vehicle_positions": f"{BASE_URL}/vehicle-positions",
    "trip_updates": f"{BASE_URL}/trip-updates",
}

# Response headers worth keeping for later analysis (cadence, throttling).
KEEP_HEADERS = {
    "etag",
    "last-modified",
    "cache-control",
    "age",
    "date",
    "x-rate-limit",
}

MAX_BACKOFF_SECONDS = 300.0

stop_event = threading.Event()


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


class FeedPoller:
    def __init__(self, name: str, url: str, interval: float, api_key: str, out_dir: Path):
        self.name = name
        self.url = url
        self.interval = interval
        self.api_key = api_key
        self.out_path = out_dir / f"{name}.ndjson"
        self.error_path = out_dir / f"{name}_errors.ndjson"
        self.backoff = 0.0
        self.attempted = 0
        self.succeeded = 0

    def poll_once(self) -> None:
        self.attempted += 1
        started = time.monotonic()
        fetch_ts = datetime.now(timezone.utc).isoformat()
        try:
            resp = requests.get(self.url, headers={"KeyId": self.api_key}, timeout=15)
        except requests.RequestException as exc:
            self._error(fetch_ts, error=f"request_exception: {exc.__class__.__name__}")
            self._bump_backoff()
            return

        latency_ms = round((time.monotonic() - started) * 1000, 2)

        if resp.status_code != 200:
            # Never echo the response body on an auth path — a 401 for a
            # recognized key repeats the key verbatim in this gateway.
            snippet = "" if resp.status_code in (401, 403) else resp.text[:300]
            self._error(
                fetch_ts,
                error=f"http_{resp.status_code}",
                status_code=resp.status_code,
                body_snippet=snippet,
            )
            self._bump_backoff()
            return

        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            decoded = MessageToDict(feed, preserving_proto_field_name=True)
        except Exception as exc:  # noqa: BLE001 — spike: log and move on
            self._error(fetch_ts, error=f"decode_error: {exc.__class__.__name__}")
            self._bump_backoff()
            return

        record = {
            "fetch_timestamp": fetch_ts,
            "fetch_latency_ms": latency_ms,
            "payload_bytes": len(resp.content),
            "response_headers": {
                k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS
            },
            "feed": decoded,
        }
        with self.out_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        self.succeeded += 1
        self.backoff = 0.0

    def _error(self, fetch_ts: str, **fields: object) -> None:
        with self.error_path.open("a") as f:
            f.write(json.dumps({"fetch_timestamp": fetch_ts, **fields}) + "\n")
        log(f"{self.name}: ERROR {fields.get('error')}")

    def _bump_backoff(self) -> None:
        self.backoff = min(MAX_BACKOFF_SECONDS, max(1.0, (self.backoff * 2) or self.interval))

    def run(self) -> None:
        log(f"{self.name}: every {self.interval}s -> {self.url}")
        while not stop_event.is_set():
            self.poll_once()
            stop_event.wait(self.backoff if self.backoff > 0 else self.interval)
        log(f"{self.name}: stopped (attempted={self.attempted} succeeded={self.succeeded})")


def _write_status(out_dir: Path, pollers: list[FeedPoller], deadline: datetime, started: datetime) -> None:
    (out_dir / "status.json").write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "started_at": started.isoformat(),
                "deadline": deadline.isoformat(),
                "feeds": {
                    p.name: {"attempted": p.attempted, "succeeded": p.succeeded}
                    for p in pollers
                },
            },
            indent=2,
        )
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=Path("../captures/vline"))
    parser.add_argument("--interval", type=float, default=10.0, help="poll interval (s) for VP + TU")
    parser.add_argument(
        "--max-runtime-hours",
        type=float,
        default=72.0,
        help="self-terminate after this many hours (0 is refused — no unbounded runs)",
    )
    args = parser.parse_args()

    if args.max_runtime_hours <= 0:
        sys.exit("--max-runtime-hours must be > 0: this spike does not run unbounded")

    api_key = os.environ.get("VIC_TRANSPORT_API_KEY")
    if not api_key:
        sys.exit("VIC_TRANSPORT_API_KEY is not set (sent as the KeyId header)")

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    deadline = started + timedelta(hours=args.max_runtime_hours)
    log(f"capture starts {started.isoformat()}, HARD STOP at {deadline.isoformat()} ({args.max_runtime_hours}h)")

    pollers = [FeedPoller(name, url, args.interval, api_key, out_dir) for name, url in FEEDS.items()]

    def handle_signal(signum: int, _frame: object) -> None:
        log(f"signal {signum} received — shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = [threading.Thread(target=p.run, name=p.name) for p in pollers]
    for t in threads:
        t.start()

    last_status = 0.0
    last_hour_log = started
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        if now >= deadline:
            log("deadline reached — stopping")
            stop_event.set()
            break
        if time.monotonic() - last_status >= 30:
            _write_status(out_dir, pollers, deadline, started)
            last_status = time.monotonic()
        if now - last_hour_log >= timedelta(hours=1):
            remaining = deadline - now
            log(f"still running — {remaining} left until hard stop")
            last_hour_log = now
        stop_event.wait(1.0)

    for t in threads:
        t.join()
    _write_status(out_dir, pollers, deadline, started)
    log("capture complete")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
M1 data spike — continuous poller for the three Metro Train GTFS-R feeds.

Polls Vehicle Positions + Trip Updates at --vp-tu-interval (default 10s) and
Service Alerts at --sa-interval (default 60s). Each successful poll appends
one line of newline-delimited JSON to data/<feed>.ndjson containing the
decoded FeedMessage plus fetch metadata (timestamp, latency, payload size).

Usage:
    export VIC_TRANSPORT_API_KEY=...
    python capture.py --out-dir data/
"""
import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from google.protobuf.json_format import MessageToDict
from google.transit import gtfs_realtime_pb2

BASE_URL = "https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro"

FEEDS = {
    "vehicle_positions": f"{BASE_URL}/vehicle-positions",
    "trip_updates": f"{BASE_URL}/trip-updates",
    "service_alerts": f"{BASE_URL}/service-alerts",
}

MAX_BACKOFF_SECONDS = 300

stop_event = threading.Event()


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] {msg}", flush=True)


class FeedPoller:
    def __init__(self, name: str, url: str, interval: float, api_key: str, out_dir: str):
        self.name = name
        self.url = url
        self.interval = interval
        self.api_key = api_key
        self.out_path = os.path.join(out_dir, f"{name}.ndjson")
        self.error_log_path = os.path.join(out_dir, f"{name}_errors.ndjson")
        self.backoff = 0.0
        self.polls_attempted = 0
        self.polls_succeeded = 0

    def poll_once(self) -> None:
        self.polls_attempted += 1
        fetch_start = time.monotonic()
        fetch_ts = datetime.now(timezone.utc).isoformat()
        headers = {"KeyId": self.api_key}
        try:
            resp = requests.get(self.url, headers=headers, timeout=10)
        except requests.RequestException as exc:
            self._record_error(fetch_ts, error=f"request_exception: {exc}")
            self._bump_backoff()
            return

        latency_ms = (time.monotonic() - fetch_start) * 1000

        if resp.status_code != 200:
            self._record_error(
                fetch_ts,
                error=f"http_{resp.status_code}",
                status_code=resp.status_code,
                body_snippet=resp.text[:500],
            )
            self._bump_backoff()
            return

        payload_bytes = resp.content
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(payload_bytes)
            decoded = MessageToDict(feed, preserving_proto_field_name=True)
        except Exception as exc:
            self._record_error(fetch_ts, error=f"decode_error: {exc}")
            self._bump_backoff()
            return

        record = {
            "fetch_timestamp": fetch_ts,
            "fetch_latency_ms": round(latency_ms, 2),
            "payload_bytes": len(payload_bytes),
            "response_headers": {
                k: v
                for k, v in resp.headers.items()
                if k.lower() in ("etag", "last-modified", "cache-control", "age", "x-ratelimit-remaining")
            },
            "feed": decoded,
        }
        self._append(self.out_path, record)
        self.polls_succeeded += 1
        self.backoff = 0.0

    def _record_error(self, fetch_ts: str, **fields) -> None:
        record = {"fetch_timestamp": fetch_ts, **fields}
        self._append(self.error_log_path, record)
        log(f"{self.name}: ERROR {fields.get('error')}")

    def _bump_backoff(self) -> None:
        self.backoff = min(MAX_BACKOFF_SECONDS, max(1.0, self.backoff * 2 or self.interval))

    @staticmethod
    def _append(path: str, record: dict) -> None:
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def run(self) -> None:
        log(f"{self.name}: polling every {self.interval}s -> {self.url}")
        while not stop_event.is_set():
            self.poll_once()
            sleep_for = self.backoff if self.backoff > 0 else self.interval
            stop_event.wait(sleep_for)
        log(
            f"{self.name}: stopped. attempted={self.polls_attempted} "
            f"succeeded={self.polls_succeeded}"
        )


def main() -> None:
    load_dotenv()  # picks up .env in the current working directory, if present
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="data", help="directory for ndjson capture files")
    parser.add_argument("--vp-tu-interval", type=float, default=10.0, help="poll interval (s) for VP + TU")
    parser.add_argument("--sa-interval", type=float, default=60.0, help="poll interval (s) for Service Alerts")
    args = parser.parse_args()

    api_key = os.environ.get("VIC_TRANSPORT_API_KEY")
    if not api_key:
        sys.exit(
            "VIC_TRANSPORT_API_KEY is not set. Register at "
            "https://opendata.transport.vic.gov.au/ and export your key "
            "(sent as the KeyId header)."
        )

    os.makedirs(args.out_dir, exist_ok=True)

    intervals = {
        "vehicle_positions": args.vp_tu_interval,
        "trip_updates": args.vp_tu_interval,
        "service_alerts": args.sa_interval,
    }
    pollers = [
        FeedPoller(name, url, intervals[name], api_key, args.out_dir)
        for name, url in FEEDS.items()
    ]

    def handle_signal(signum, frame):
        log(f"received signal {signum}, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = [threading.Thread(target=p.run, name=p.name) for p in pollers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log("all pollers stopped.")


if __name__ == "__main__":
    main()

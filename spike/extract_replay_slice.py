#!/usr/bin/env python3
"""One-off: extract a bounded time-slice from the M1 capture for 2d's replay
fixture. Streams the source ndjson (never loads it whole) and writes only
lines whose fetch_timestamp falls in [--start, --end) to --out."""
import argparse
import json
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument("--src", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--start", required=True, help="ISO8601, e.g. 2026-07-18T18:30:00+00:00")
parser.add_argument("--end", required=True)
args = parser.parse_args()

start = datetime.fromisoformat(args.start)
end = datetime.fromisoformat(args.end)

n_in, n_out = 0, 0
with open(args.src) as src, open(args.out, "w") as out:
    for line in src:
        n_in += 1
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        ts = datetime.fromisoformat(rec["fetch_timestamp"])
        if start <= ts < end:
            out.write(line + "\n")
            n_out += 1

print(f"{args.src}: {n_in} lines in, {n_out} lines written to {args.out}")

# V/Line regional-rail data spike

Spike-grade, standalone (no import of the production service). Answers the
open Phase-A questions for V/Line regional train support: a short raw
capture of the Vehicle Positions + Trip Updates feeds plus a pinned mode-1
static GTFS snapshot, then an offline analyzer.

Service Alerts is **not** captured — V/Line ships without it.

## Layout

| File | Purpose |
|---|---|
| `capture_vline.py` | Polls VP + TU, appends decoded NDJSON. Self-terminates after `--max-runtime-hours` (default 72; `0` is refused). |
| `pin_static.py` | Downloads the outer GTFS zip-of-zips once, extracts **only** `1/google_transit.zip`, deletes the outer archive, writes a snapshot + manifest. |
| `analyze_vline.py` | Turns the capture + snapshot into `report.md` — measurements only; the decision calls go in `artifacts/vline-spike-findings.md`. |

Captures land under `spike/captures/vline/` (gitignored).

## Run (on the capture host)

```sh
cd spike/vline
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

export VIC_TRANSPORT_API_KEY=...          # sent as the KeyId header

python pin_static.py --out-dir ../captures/vline

# 72h window across a weekday peak, an off-peak, and a weekend day.
# The process stops itself; do NOT wrap it in an unbounded nohup.
python capture_vline.py --out-dir ../captures/vline --max-runtime-hours 72
```

Check progress any time via `../captures/vline/status.json`.

## Hard-stop rule

A prior V/Line capture was launched with `nohup ... &` and no stop and ran
unattended for ~5 days. `capture_vline.py` owns its own deadline and exits
at it. If you must background it, use something that still bounds it
(`systemd-run --user --scope -p RuntimeMaxSec=...`, `timeout`, a `screen`
you actually return to) — never an unbounded `nohup ... & disown`.

## Analyze

```sh
python analyze_vline.py \
  --capture-dir ../captures/vline \
  --snapshot ../captures/vline/mode1_snapshot.zip \
  --out report.md
```

Then write `artifacts/vline-spike-findings.md` (same structure as the
Metro data spike's own findings doc: measured numbers -> decision matrix
-> planning resolution) and resolve the remaining open checklist items
(see `analyze_vline.py`'s own docstring for the full numbered list).

## Teardown

Once findings are written: stop any running capture, delete
`spike/captures/vline/` (raw capture + snapshot), and remove the venv. The
`spike/vline/` scripts stay in the repo as the record of method.

# AI System Card

This document covers the AI layer only — the parts of train-tracker that call
an LLM. Everything else (polling, state derivation, the public API/map) is
plain deterministic code and out of scope here.

## What it does

Two live features, both narrow: an LLM narrates numbers that plain Python has
already computed. The LLM is never the source of a fact or a statistic.

- **Weekly performance digest** — once a week, narrates an already-computed
  rollup (on-time / late / cancelled counts, per-line ranking) over the
  previous 7 days.
- **On-demand disruption briefing** — on request only, summarises currently
  active service alerts.

Both route through the same small set of read-only tools: current status of
a line, current status of an individual trip, and currently active alerts.
Nothing else is exposed to the model.

## Inputs & provenance

- Inputs are limited to this service's own already-derived state: train
  positions/status, active service alerts, trip completion records. Never
  raw upstream feed bytes, and never user-submitted free text (there is no
  live endpoint that would accept any).
- Feed text (e.g. an alert's description) is treated as **untrusted data**,
  passed to the model only as tool-result content — never concatenated into
  the system prompt as an instruction. This is a deliberate defence against
  prompt injection via upstream content the project doesn't control.
- Every call is fully traced: prompt version, inputs, outputs, and cost are
  reconstructable after the fact, not just logged as a line of text.

## Known failure modes

- **Coarse alert-to-line matching.** The upstream alert feed doesn't carry
  per-trip identifiers, so matching an alert to a line is a wildcard join,
  not a per-trip confirmation — a briefing can over- or under-attribute an
  alert.
- **Occasional low-signal output.** An alert that's missing route or stop
  detail can still slip past the pre-LLM filter and produce a briefing that
  says little of substance, despite the filter existing specifically to
  catch this.
- **Positional uncertainty compounds into narration.** "Ghost"/"coasting"
  train positions are themselves inferred, not observed — a briefing citing
  one is only as reliable as that underlying inference.
- **Fails closed on budget exhaustion.** Once the fixed monthly spend cap is
  hit, further briefings and the weekly digest are skipped outright, not
  silently degraded to a cheaper/worse response.
- **Honest cold starts.** A digest generated before a full 7-day window of
  history exists reports the partial window explicitly, rather than a
  percentage that looks complete but isn't.

## Monitoring

- **Spend is metered, not just logged.** A hard monthly cap is checked
  before every call and incremented after; calls are refused once it's
  reached.
- **Every call is traced end-to-end** — prompt, inputs, outputs, cost, and
  errors are all recorded per call, so any output can be reconstructed and
  audited after the fact.
- **Dedicated metrics** track briefings sent (with reason), alongside the
  project-wide dashboards and alerts for feed staleness, error rate, and
  resource exhaustion.

## Evaluation

- A small, deterministic eval set runs before shipping any prompt or filter
  change: a clear single-line alert, multiple simultaneous alerts, and two
  cases that must be filtered out *before* reaching the LLM at all (an alert
  with no route information, and no active alerts). Scored on fixed
  keyword/length checks — deliberately not LLM-as-judge, to avoid one
  model's non-determinism grading another's.
- Run manually, not on every commit — each run spends real money.
- The planned delay-prediction model will be evaluated against a naive
  baseline ("assume the current delay doesn't change") before being trusted,
  with train/test split by service day so the same trip never leaks across
  both sides.

## Labelling discipline: prediction vs. fact

This is the project's actual AI-governance thesis: every AI-touched value is
labelled by the evidence behind it, never asserted as plain ground truth.

- Trip data carries a `position_source` field — `"live"` (this cycle's real
  feed data) versus `"last_confirmed"` (a real earlier position, held during
  a coverage gap). The model is instructed to narrate anything other than
  `"live"` as uncertain, never as a current fact.
- Ghost/coasting trips carry how long they've been ungrounded, so a
  narrated position can be qualified ("last confirmed 4 minutes ago")
  instead of stated flatly as current.
- The planned delay prediction will carry the same discipline: presented as
  a prediction with its evidence cited (current delay, stops remaining,
  active alerts), never phrased as an arrival time.
- Numbers — on-time percentages, delay counts, per-line rankings — are
  always computed by plain, auditable code and only ever narrated by the
  LLM. The model is never the source of a number, only its explainer.

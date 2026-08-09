---
pretty_name: train-tracker Historical Archive
tags:
  - public-transit
  - melbourne
  - time-series
language: en
---

# Dataset Card — train-tracker historical archive

**Note on the metadata block above:** deliberately does not include a
`license` field yet — that stays undecided until this repo's future
public-flip legal review (Terms of Use check, attribution wording), so
this card doesn't imply a licence has been settled when it hasn't.

This document explains what's inside this dataset and how to read it
correctly. It assumes no prior knowledge of Melbourne's train data or of
this project's internal terminology — every concept it uses is defined
here before it's used.

**Status: this dataset is currently PRIVATE.** The legal review needed
before any public release (checking the data source's terms of use,
writing proper attribution wording) hasn't happened yet — that's a
deliberate decision, not something forgotten. Nothing in this card should
be treated as permission to redistribute this data publicly.

## Background: where this data comes from

Melbourne's train operator (via Victoria's state transport department)
publishes two live data feeds that update constantly throughout the day:

- **"Vehicle Positions"** — where each train physically is right now
  (GPS-style coordinates), refreshed roughly every 30 seconds.
- **"Trip Updates"** — how each train is tracking against its timetable
  (which stops it's due at, and when), refreshed roughly every 10 seconds
  or faster.

This project (train-tracker) polls both feeds continuously, combines them
into one picture of "what is happening on the network right now," and — as
of this dataset — permanently archives that picture, one day at a time, so
it isn't lost once the live system's short-term storage rolls it off.

**Important: the government feeds themselves are not perfect.** They don't
cover every train at every moment, they sometimes disagree with each other,
and trains can drop off the radar for a while before reappearing. This
dataset doesn't hide any of that — it deliberately records those rough
edges rather than smoothing them over, because knowing when the data is
uncertain is treated as just as important as the data itself.

## The five tables, explained one at a time

Everything is organised into five separate tables. Each row is one
*event* — something that was true, or that happened, at a specific point
in time. There's one Parquet file (a compact, efficient file format for
data analysis, similar in purpose to a CSV but far smaller and faster to
query) per table per calendar day of train service.

### 1. `trip_completion_events` — did the train arrive on time?

One row per completed trip. Records when a train was scheduled to arrive
at its final stop, when it actually did, and how late (or early) it was.
This is the simplest table to reason about: it's a straightforward
scheduled-vs-actual comparison, computed after the trip has finished.

### 2. `delay_observation_events` — how late was the train while it was still running?

One row per delay reading taken *during* a trip, not just at the end.
While a train is en route, the government feed periodically publishes its
own estimate of how many seconds behind (or ahead of) schedule it
currently is — this table captures those in-progress readings. Useful for
questions like "how did this train's delay build up over the journey?"
rather than just "was it late at the end?"

### 3. `ghost_events` — when a train temporarily vanished from the feed

This is the table most likely to need explaining, because "ghost" is this
project's own term, not an official one.

The government feed doesn't track 100% of trains 100% of the time — trains
can disappear from the live data for anywhere from a few seconds to
several minutes, then reappear. When train-tracker's live map detects this
happening to a train it was previously tracking, it doesn't just make the
train vanish from the screen. Instead it goes through a sequence of
states:

1. **Live** — the train is currently reporting position data normally.
2. **Coasting** — the train just stopped reporting, but only briefly (up
   to about 60–90 seconds). The map keeps showing it moving smoothly along
   its last known path and direction, labelled as "last seen Xs ago,"
   rather than freezing or disappearing outright — most brief drop-outs
   are just a missed poll, not a real problem.
3. **Ghost** — if the train still hasn't reappeared after the coasting
   window, the map switches to showing where the train's *timetable* says
   it should be right now, visually marked as an estimate rather than a
   real position — being upfront that this is a guess, not a
   measurement.
4. The train either reappears (feed resumes) or the trip ends and it fades
   from tracking.

**A `ghost_events` row records one full episode of this** — from the
moment a train was last genuinely seen to the moment (if any) it
reappeared, including where it was last seen and where it turned up again.
It does **not** contain any invented positions from during the gap itself
— only real observed endpoints.

Every episode also has a `reason` column explaining why it ended: the
train reappeared normally, the episode hit this project's own maximum
tracking window with no explanation, the recording process shut down
mid-episode, or — the two most informative cases — the trip was
independently confirmed to have **finished its scheduled run** or been
**cancelled** by the government feed, in which case the episode is closed
out as soon as that's known rather than left showing as an unexplained gap.
`reason` was added in `schema_version` 2 (rows from before that carry
`schema_version` 1 and a null `reason` — see "Schema changes over time"
below).

### 4. `discrepancy_events` — when the two government feeds disagreed

Because train-tracker combines two separately-updating feeds (Vehicle
Positions and Trip Updates, described above), there are moments where they
briefly tell a different story about the same train — e.g. one feed has
already picked up a train's new trip while the other hasn't caught up yet.
This table logs every one of those disagreements, along with what each
feed said.

Most of these are harmless timing artefacts, not real data problems — see
"Known caveats" below for the actual measured breakdown.

### 5. `poll_gap_events` — when the whole capture system itself had an outage

This table is different from the other four: it's not about any individual
train, it's about *this project's own polling process* failing or being
paused (e.g. the government feed itself was unreachable for a while). Each
row is one outage window: when it started, when it ended, and why.

This table matters for interpreting all the others — see "Reading around
outages" below.

## What every column means: the four "provenance classes"

Every single column in every table is labelled as one of four types, so
that anyone reading the data always knows *where a value actually came
from* and how much to trust it:

- **DTP-observed** — a fact taken directly from the government feed as-is:
  a train's raw GPS position, or a real recorded arrival time once a trip
  has actually finished. ("DTP" is Victoria's Department of Transport and
  Planning, the government body that publishes this data.)
- **DTP-predicted** — the government feed's own *forecast* about the
  future, not something that has happened yet. For example, "this train is
  currently running 3 minutes late" is a live estimate that can change or
  turn out to be wrong — it is not the same kind of fact as an arrival
  time that's already happened. Never treat a DTP-predicted value as
  certain.
- **archive-derived** — a value this project calculated itself from other
  data, rather than something the government feed said directly. For
  example, "this train was 47 seconds late" (`delay_seconds`) is worked
  out by comparing the scheduled and actual times, not read directly off
  the feed.
- **capture-metadata** — a fact about *this project's own recording
  process*, not about the train at all — e.g. the exact moment this
  system happened to record something. Useful for debugging or auditing,
  not for describing train behaviour.

## Reading around outages (read this before running any query)

Because `poll_gap_events` records when this project's own data collection
had an outage, some rows in the *other* four tables are not real
observations at all — they're placeholder "gap marker" rows, automatically
inserted to make outages visible inside every table, not just the outage
log itself.

A gap-marker row has `is_gap_marker` set to `true`, every real-data column
left empty, and three extra columns filled in instead:
`gap_started_at`, `gap_ended_at`, and `gap_reason` (why the outage
happened).

**Always exclude these before treating a table as a record of real
events.** For example:

```sql
SELECT * FROM ghost_events WHERE NOT is_gap_marker;
```

Why this matters: if these marker rows didn't exist, a table queried on
its own during an outage would look exactly like "nothing happened that
day" — which is misleading. It should instead say "we don't know what
happened during this window," which is a very different, more honest
statement. This project treats that distinction as important enough to
build into the data itself, rather than leaving it as something a reader
has to remember separately.

## Known caveats (read before drawing conclusions from this data)

- **The government feed does not track every train, all the time.**
  Compared to the full published timetable, live tracking coverage
  typically runs at roughly 83–92% of scheduled trains during the day,
  dropping as low as roughly one-third of trains overnight. A train that
  isn't currently in the live feed doesn't necessarily mean it isn't
  running — it may just not be reporting.
- **Positions during a "ghost" episode are an educated guess, not a
  measurement.** While a train is missing from the feed, this project's
  live map shows where the timetable says it should be — but this dataset
  only ever records the real, observed start and end points of that gap,
  never any invented positions in between.
- **Melbourne's underground "City Loop" causes some brief, harmless data
  gaps — but it's not the main cause of ghosting.** The City Loop is an
  underground rail tunnel beneath Melbourne's CBD; trains inside it
  sometimes briefly lose GPS reception, similar to a car losing signal in
  a tunnel. In this project's data, that shows up as a short freeze
  (typically around 20 seconds — no different from a train just sitting
  at a normal stop) and, rarely, a "jump" to a new position once signal
  returns (worst case observed: about 863 metres). Despite this, when
  measured over a full week of real data, almost none of the recorded
  `ghost_events` episodes were fully explained by City Loop coverage —
  so most gaps in the data are caused by something else entirely, not the
  tunnel.
- **Most `discrepancy_events` rows are timing noise, not real
  disagreements.** Because the two source feeds update at different
  speeds, the far more common case is simply one feed picking up a change
  a few seconds before the other one does — not a genuine conflict about
  the facts. Measured over a week of real data: fewer than 0.2% of all
  discrepancies were an actual disagreement about a train's scheduled
  start time, and there were zero disagreements about which route a train
  was even on.
- **Some text columns can contain new values over time.** Columns like
  `discrepancy_type` (on `discrepancy_events`), `reason` (on
  `poll_gap_events`), and `status` (on `trip_completion_events`) are
  plain, open-ended text, not a fixed list of allowed values — this
  project's own classification logic can add new categories in future,
  so don't assume you've seen every possible value just because you've
  seen this dataset before.
- **This dataset contains no information about passengers.** Every row
  describes a train (its position, timing, or tracking status) or this
  project's own data-collection process — never any information about who
  was on board. There has never been a way for this system to collect
  that information in the first place.

## Schema changes over time

Every row carries a `schema_version` integer. Columns are only ever added,
never removed or renamed — but a column added partway through means
earlier days' files genuinely don't have it. **Loading every day's files
together with a library that assumes one fixed schema across all of them
can fail or silently drop data** — verified directly against this
project's own files:

- `datasets.load_dataset("parquet", data_files=[...])` raises a hard
  `CastError` if the files don't all share identical columns.
- `pyarrow.dataset.dataset(...)` does **not** error — it silently infers
  the schema from whichever file it reads first, and any column absent
  from that file is dropped from the whole result, even for rows from
  files that do have it. This is the more dangerous failure mode because
  nothing signals that data went missing.

**The correct way to load the full history:** compute the union schema
across all files first (`pyarrow.unify_schemas`), then pass it explicitly
— `pyarrow.dataset.dataset(..., schema=unified)`, or
`datasets.load_dataset(..., features=Features.from_arrow_schema(unified))`
— so older rows get the new column filled with null instead of the column
disappearing or the load failing outright.

Current schema versions:

| `schema_version` | Change |
|---|---|
| 1 | Initial archival schema (five tables, as described above) |
| 2 | Added `ghost_events.reason` |

## How often this dataset updates

Once per night, this project's automated archiving job adds the
previous day's (or days') data. If a night is missed for any reason (e.g.
the server was briefly down), the very next run automatically catches up
on everything that was missed — a missed night never becomes a permanently
missing day as long as the underlying data still exists locally.

## Legal and attribution information (not yet included)

This section is deliberately incomplete for now, because this dataset is
still private. Once (and if) it's made public, this section will be
replaced with: where the underlying data comes from and under what
licence, exactly what changes this project made to it, a clear statement
that this project is independent and not endorsed by the government body
that publishes the original data, and confirmation that it contains no
personal information (already true today, per the caveat above — this
will simply be stated formally). Until that happens, treat this dataset as
for internal use only.

"""Response-shape contract -- defined once, explicitly, before the routes
that use it, rather than letting FastAPI infer a shape implicitly from
whatever the state store happens to hold.

Every timestamp is UTC (project-wide convention). Feed staleness is included
per-feed in every response -- the poller's "0 entities but header still
advancing" honesty (CLAUDE.md's settled staleness decision) has to survive
into the public API, not just live on the internal dashboard.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class FeedStatus(BaseModel):
    last_changed_at: datetime | None
    stale: bool


class Train(BaseModel):
    trip_id: str
    route_id: str | None
    status: str  # "live" | "coasting" | "ghost"
    latitude: float | None
    longitude: float | None
    bearing: float | None
    position_updated_at: datetime | None
    schedule_updated_at: datetime | None
    # Static-schedule join (M12), resolved from the trip's own service_date
    # -- None whenever no snapshot is pinned yet, or the trip is a
    # real-time-only ADDED trip with no static trips.txt row at all.
    start_time: str | None
    trip_headsign: str | None
    direction_id: int | None
    # Rolling-window-aware "what's next" (M12 #2) -- all three null
    # together whenever the window hasn't surfaced a next stop yet (see
    # `state/station.py`'s `next_stop_and_delay`), never a crash.
    # `next_stop_delay_seconds` is signed: positive late, negative early.
    next_stop_id: str | None
    next_stop_name: str | None
    next_stop_delay_seconds: int | None
    # Trip progress (M12 #5: "3 of 12 stops done") -- `progress_stop_sequence`
    # is absolute (see `state/station.py`'s `current_stop_sequence` for why
    # that stays meaningful despite TU's rolling window), `progress_total_stops`
    # is the trip's static terminus stop_sequence. Both null together for a
    # real-time-only ADDED trip (no static stop_times.txt row to total
    # against) or when the rolling window hasn't surfaced any anchor yet.
    progress_stop_sequence: int | None
    progress_total_stops: int | None
    # Distinct from position_updated_at: set for every train regardless of
    # whether it's still present in the live feeds, so a fully-vanished
    # ghost (route_id/position_updated_at all null -- see api/app.py) still
    # carries an honest "last confirmed at" timestamp instead of looking
    # like a fresh, currently-unlocated train.
    last_seen_at: datetime | None


class StateResponse(BaseModel):
    generated_at: datetime
    backoff_active: bool
    feeds: dict[str, FeedStatus]
    trains: list[Train]


class DeltaResponse(BaseModel):
    """SSE `delta` event body, computed per-connection against whatever
    that connection was sent last (no shared ring buffer -- deliberate
    scope cut). `changed` is any train (new or updated) whose fields
    differ from last time; `removed` is trip_ids that were reported last
    time but no longer are (e.g. aged past api/app.py's MAX_GHOST_AGE_S)."""

    generated_at: datetime
    changed: list[Train]
    removed: list[str]


class HealthResponse(BaseModel):
    status: str


class ScheduledTrain(BaseModel):
    """One upcoming departure at a station: the static-schedule time is
    always present; `predicted_time`/`delay_seconds` are only set when a
    live Trip Updates prediction for this exact (trip_id, platform) exists
    right now -- `is_live` makes that distinction explicit rather than
    letting callers guess from nullability alone (same "label every
    inference" spirit as the ghost/discrepancy schemas elsewhere).

    `is_cancelled`: true when TU's `schedule_relationship` marks either
    the whole trip CANCELED or, more narrowly, just this platform's stop
    SKIPPED (the train runs but doesn't call here) -- both mean "don't
    expect this departure," so both collapse to one flag rather than
    exposing the raw enum distinction to callers who don't need it.

    `is_added`: true for a real-time-only extra service -- TU's
    `schedule_relationship` is ADDED, meaning it has no static
    `stop_times.txt` row at all. `headsign` for these is derived (final
    stop's name), not schedule fact, since ADDED trips carry none."""

    trip_id: str
    route_id: str
    direction_id: int | None
    headsign: str
    scheduled_time: datetime
    predicted_time: datetime | None
    delay_seconds: int | None
    is_live: bool
    is_cancelled: bool
    is_added: bool
    platform_code: str | None


class LineSummary(BaseModel):
    route_id: str
    short_name: str
    long_name: str


class StationScheduleResponse(BaseModel):
    station_id: str
    generated_at: datetime
    # GTFS enum, straight from stops.txt: 0=unknown, 1=accessible,
    # 2=not accessible. Null when the station's parent-station row itself
    # doesn't carry the field.
    wheelchair_boarding: int | None
    departures: list[ScheduledTrain]
    # M12 #3: lines that normally call here but have zero calendar-active
    # trips today anywhere on the network -- see
    # `PinnedScheduleCache.lines_no_service_today`'s docstring for why this
    # is system-wide, not station-specific. Empty, not omitted, when
    # nothing's suspended today.
    lines_no_service_today: list[LineSummary]


class AlertActivePeriod(BaseModel):
    start: datetime | None
    end: datetime | None


class AlertInformedEntity(BaseModel):
    """One route/stop/direction this alert's scope covers -- any field can
    be null, meaning "unspecified" (applies broadly on that axis), per the
    upstream feed. There is no trip_id here at all: this is a coarse join,
    never confirmation that a specific train is affected -- see
    `state/alerts.py`."""

    route_id: str | None
    route_name: str | None
    stop_id: str | None
    direction_id: int | None


class Alert(BaseModel):
    id: str
    cause: str | None
    effect: str | None
    header_text: str | None
    description_text: str | None
    url: str | None
    active_periods: list[AlertActivePeriod]
    informed_entities: list[AlertInformedEntity]


class AlertsResponse(BaseModel):
    generated_at: datetime
    alerts: list[Alert]


class BriefingTriggerResponse(BaseModel):
    """`sent=False` covers three distinct, deliberately-not-conflated
    cases (see `reason`): nothing currently active is specific enough to
    brief (ai/briefing_filter.py), the monthly budget cap is reached, or
    composition/delivery failed -- a caller triggering this by hand wants
    to know WHICH, not just that nothing arrived in Slack."""

    sent: bool
    reason: str | None = None
    text: str | None = None


class WeeklyLineStat(BaseModel):
    route_id: str
    trip_count: int
    on_time_count: int
    late_count: int
    cancelled_count: int
    on_time_pct: float


class WeeklyDigest(BaseModel):
    """One week's performance digest. `days_covered` surfaces a partial
    window honestly (cold start / a mid-week outage day) rather than
    presenting fewer than 7 days of data as if it were a complete week --
    same gap-honesty convention as `ScheduledTrain.is_live`/`is_added`
    elsewhere in this schema module."""

    week_start: date
    week_end: date
    days_covered: int
    on_time_count: int
    late_count: int
    cancelled_count: int
    on_time_pct: float
    narrative: str
    slack_delivered: bool
    line_stats: list[WeeklyLineStat]


class WeeklyDigestListResponse(BaseModel):
    digests: list[WeeklyDigest]


class AttributionResponse(BaseModel):
    """A license condition, not a nicety -- static content, deliberately
    its own endpoint rather than embedded in every `/api/state`/SSE
    payload (CC BY 4.0 requires the credit be findable and displayed
    somewhere tied to the data's use, not restated on every single packet)."""

    source: str
    license: str
    license_url: str
    note: str


class ArchiveStatusResponse(BaseModel):
    """The one public-safe fact from the (otherwise fully internal) nightly
    Hugging Face archive pipeline -- see `archive/public_status.py`'s
    docstring for why this is deliberately the only thing exposed.
    `last_archived_date` is `None` if the archiver has never completed a
    successful pass yet (not the same as the feature being unconfigured,
    which is a 503, not a 200 with a null field)."""

    last_archived_date: date | None


class InsightsLineStat(BaseModel):
    """One real line's rollup for the requested range -- never a `-R`
    (replacement bus) row: a substitute bus doesn't undo a cancelled,
    non-delivered scheduled trip, so `-R` volume is surfaced only via
    `replacement_bus_count`, never merged into these counts."""

    route_id: str
    on_time_count: int
    late_count: int
    cancelled_count: int
    gap_count: int
    replacement_bus_count: int


class InsightsHourlyStat(BaseModel):
    """Completions at terminus bucketed by Melbourne-LOCAL hour of arrival
    -- an arrival proxy, not a departure-frequency count. `route_id=None`
    means network-wide (all real lines summed), used for the "All lines"
    view."""

    route_id: str | None
    hour_local: int
    completion_count: int


class InsightsHistogramStat(BaseModel):
    """Network-wide delay distribution for the selected range. Bucket
    boundaries deliberately do NOT match a naive "1-5min/5-10min/10+min"
    split -- that would double-count against the on-time threshold
    (<=4:59); buckets here start where "late" actually starts. `gap_count`
    included for the same honesty reason undetermined_gap is never folded
    away elsewhere in this dashboard."""

    on_time_count: int
    late_5_10_count: int
    late_10_plus_count: int
    cancelled_count: int
    gap_count: int


class InsightsResponse(BaseModel):
    """Backs the Insights dashboard's global date-range filter: one
    response per selected range (Today / Yesterday / Last 7 days / Last 30
    days / Custom), never per-chart -- the frontend slices this one
    payload across all charts.

    `days_covered` vs. `expected_days` is the partial-range honesty
    signal: a deployment only 2 days old returns 2 covered days against
    an expected_days of 7 for "Last 7 days" -- the UI's "(N of 7 days)"
    indicator is built directly from this pair, not inferred from date
    arithmetic client-side.

    `generated_at_by_date` backs the staleness tooltip: only
    "today" has a genuine freshness concept (closed days are finalized
    once and never touched again), so the frontend looks up whichever
    date it cares about rather than this response collapsing to one
    timestamp."""

    range_name: str
    days_covered: list[date]
    expected_days: int
    line_stats: list[InsightsLineStat]
    hourly_stats: list[InsightsHourlyStat]
    generated_at_by_date: dict[date, datetime]
    # The FULL requested date range (ascending, never past "today"),
    # regardless of which of those dates actually have a rollup yet --
    # distinct from days_covered above. Added so a per-day chart (e.g.
    # cancellations/delays over time) can render every day the user
    # selected, with an explicit gap for a day with no rollup, instead of
    # silently compressing its x-axis down to whatever happens to be
    # covered.
    requested_dates: list[date]
    # UNSUMMED per-day breakdown: the summed line_stats above can't back
    # a cancellations/delays-over-time chart or a weekday-vs-weekend
    # chart -- both need a point per day, not one range total. Keyed by
    # ISO date string (Pydantic dict keys serialize as strings regardless
    # of the Python-side date key type, matching generated_at_by_date's
    # existing behavior).
    daily_line_stats: dict[date, list[InsightsLineStat]]
    histogram_stats: InsightsHistogramStat

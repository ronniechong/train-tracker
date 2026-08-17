// Mirrors service/src/traintracker/api/schemas.py — the public API's
// response contract. Kept as plain interfaces, not generated, since the
// shape is small and stable (M3's finding #10: defined once, explicitly).

export type TrainStatus = 'live' | 'coasting' | 'ghost'

export interface FeedStatus {
  last_changed_at: string | null
  stale: boolean
}

export interface Train {
  trip_id: string
  route_id: string | null
  status: TrainStatus
  latitude: number | null
  longitude: number | null
  bearing: number | null
  position_updated_at: string | null
  schedule_updated_at: string | null
  last_seen_at: string | null
  start_time: string | null
  trip_headsign: string | null
  direction_id: number | null
  // Rolling-window-aware "what's next" (M12 #2) -- all three null together
  // whenever the window hasn't surfaced a next stop yet. delay_seconds is
  // signed: positive late, negative early.
  next_stop_id: string | null
  next_stop_name: string | null
  next_stop_delay_seconds: number | null
}

export interface StateResponse {
  generated_at: string
  backoff_active: boolean
  feeds: Record<string, FeedStatus>
  trains: Train[]
}

export interface DeltaResponse {
  generated_at: string
  changed: Train[]
  removed: string[]
}

export interface Attribution {
  source: string
  license: string
  license_url: string
  note: string
}

// The one public-safe fact from the nightly Hugging Face archive
// pipeline -- see the backend's archive/public_status.py docstring for
// why nothing else from that pipeline is exposed here. `null` means the
// archiver is wired up but hasn't completed a successful pass yet, not
// the same as the endpoint being unavailable (a 404/503, handled by the
// hook rendering nothing).
export interface ArchiveStatus {
  last_archived_date: string | null
}

export interface ScheduledTrain {
  trip_id: string
  route_id: string
  direction_id: number | null
  headsign: string
  scheduled_time: string
  predicted_time: string | null
  delay_seconds: number | null
  is_live: boolean
  is_cancelled: boolean
  is_added: boolean
}

export interface LineSummary {
  route_id: string
  short_name: string
  long_name: string
}

export interface StationScheduleResponse {
  station_id: string
  generated_at: string
  departures: ScheduledTrain[]
  // M12 #3: lines that normally call here but have zero calendar-active
  // trips today anywhere on the network. Empty, not omitted, when nothing
  // is suspended today.
  lines_no_service_today: LineSummary[]
}

export interface AlertActivePeriod {
  start: string | null
  end: string | null
}

// No trip_id here at all -- any field can be null, meaning "unspecified"
// on that axis. This is a coarse route/stop/direction join from the
// upstream feed, never confirmation that a specific train is affected.
export interface AlertInformedEntity {
  route_id: string | null
  route_name: string | null
  stop_id: string | null
  direction_id: number | null
}

export interface Alert {
  id: string
  cause: string | null
  effect: string | null
  header_text: string | null
  description_text: string | null
  url: string | null
  active_periods: AlertActivePeriod[]
  informed_entities: AlertInformedEntity[]
}

export interface AlertsResponse {
  generated_at: string
  alerts: Alert[]
}

export interface WeeklyLineStat {
  route_id: string
  trip_count: number
  on_time_count: number
  late_count: number
  cancelled_count: number
  on_time_pct: number
}

// `on_time_pct` is 0-100, and only meaningful when `days_covered > 0` --
// a cold-start week with no data yet is a plain 0.0, not null. Any
// renderer must check `days_covered`/counts, not just print the number.
export interface WeeklyDigest {
  week_start: string
  week_end: string
  days_covered: number
  on_time_count: number
  late_count: number
  cancelled_count: number
  on_time_pct: number
  narrative: string
  slack_delivered: boolean
  line_stats: WeeklyLineStat[]
}

export interface WeeklyDigestListResponse {
  digests: WeeklyDigest[]
}

// M8 Insights (milestones/08-analytics-insights.md). `route_id` is never
// a `-R` (replacement bus) id here -- see the backend's PTV-methodology
// correction -- `replacement_bus_count` is the only place that volume
// shows up, kept separate from on_time/late/cancelled.
export interface InsightsLineStat {
  route_id: string
  on_time_count: number
  late_count: number
  cancelled_count: number
  gap_count: number
  replacement_bus_count: number
}

// `route_id: null` means network-wide (all real lines summed).
export interface InsightsHourlyStat {
  route_id: string | null
  hour_local: number
  completion_count: number
}

// Chart 3. Buckets diverge from the milestone doc's original
// on-time/1-5min/5-10min/10+min sketch -- that overlapped the already-
// locked <=4:59 on-time threshold. Network-wide, matching the KPI row.
export interface InsightsHistogramStat {
  on_time_count: number
  late_5_10_count: number
  late_10_plus_count: number
  cancelled_count: number
  gap_count: number
}

export type InsightsRangeName = 'today' | 'yesterday' | 'last7' | 'last30' | 'custom'

// `days_covered` vs `expected_days` is the partial-range honesty signal --
// a deployment younger than the requested window returns fewer covered
// days than requested. `generated_at_by_date` only has a genuine
// freshness meaning for "today" (closed days are finalized once and never
// touched again) -- look up the specific date you care about, don't
// collapse this to one timestamp.
export interface InsightsResponse {
  range_name: InsightsRangeName
  days_covered: string[]
  expected_days: number
  line_stats: InsightsLineStat[]
  hourly_stats: InsightsHourlyStat[]
  generated_at_by_date: Record<string, string>
  // UNSUMMED per-day breakdown, keyed by ISO date -- needed for any chart
  // that wants a point per day (over-time / weekday-vs-weekend), since
  // line_stats above is summed across the whole selected range.
  daily_line_stats: Record<string, InsightsLineStat[]>
  // The FULL requested date range, ascending -- a superset of
  // days_covered. A per-day chart should render one point per entry
  // here (zero-filled for a date missing from daily_line_stats), not
  // just whichever dates happen to have a rollup.
  requested_dates: string[]
  histogram_stats: InsightsHistogramStat
}

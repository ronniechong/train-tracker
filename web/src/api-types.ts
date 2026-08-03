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

export interface StationScheduleResponse {
  station_id: string
  generated_at: string
  departures: ScheduledTrain[]
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
// a cold-start week with no data yet is a plain 0.0, not null (see
// work-docs milestones/05-ai-layer.md's "known sharp edge" note). Any
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

export type InsightsRangeName = 'today' | 'yesterday' | 'last7' | 'last30' | 'custom'

// `days_covered` vs `expected_days` is the partial-calendar-period honesty
// signal -- "Last 7 days" picked partway through the ISO week returns
// fewer covered days than 7. `generated_at_by_date` only has a genuine
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
}

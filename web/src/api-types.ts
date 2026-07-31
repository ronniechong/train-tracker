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
}

export interface StationScheduleResponse {
  station_id: string
  generated_at: string
  departures: ScheduledTrain[]
}

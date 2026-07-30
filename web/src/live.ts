import { API_BASE_URL } from './config'
import type { DeltaResponse, FeedStatus, StateResponse, Train } from './api-types'

// Feed staleness/backoff live only on StateResponse, but the SSE stream
// only sends a full StateResponse once, at connection open (see
// api/app.py's `_event_source` -- every event after that is a DeltaResponse,
// which carries neither field). A long-lived connection would otherwise
// show status info that silently goes stale, which is exactly the kind of
// dishonesty this project's staleness design exists to avoid -- so a cheap
// periodic GET /api/state keeps it honest, independent of the SSE stream.
// 30s is comfortably under the API's 120 req/60s per-IP limit.
const STATUS_POLL_INTERVAL_MS = 30_000

export type ConnectionState = 'connecting' | 'live' | 'reconnecting'

export interface LiveState {
  trains: Map<string, Train>
  feeds: Record<string, FeedStatus>
  backoffActive: boolean
  connection: ConnectionState
}

export type LiveListener = (state: LiveState) => void

export function startLiveFeed(onUpdate: LiveListener): void {
  const state: LiveState = {
    trains: new Map(),
    feeds: {},
    backoffActive: false,
    connection: 'connecting',
  }

  const source = new EventSource(`${API_BASE_URL}/api/stream`)

  source.addEventListener('open', () => {
    state.connection = 'live'
    onUpdate(state)
  })

  source.addEventListener('error', () => {
    // EventSource retries on its own; this only affects what the status
    // indicator honestly shows in the meantime.
    state.connection = 'reconnecting'
    onUpdate(state)
  })

  source.addEventListener('snapshot', (event) => {
    const data: StateResponse = JSON.parse((event as MessageEvent).data)
    state.trains = new Map(data.trains.map((train) => [train.trip_id, train]))
    state.feeds = data.feeds
    state.backoffActive = data.backoff_active
    state.connection = 'live'
    onUpdate(state)
  })

  source.addEventListener('delta', (event) => {
    const data: DeltaResponse = JSON.parse((event as MessageEvent).data)
    for (const train of data.changed) {
      state.trains.set(train.trip_id, train)
    }
    for (const tripId of data.removed) {
      state.trains.delete(tripId)
    }
    onUpdate(state)
  })

  async function pollStatus(): Promise<void> {
    try {
      const response = await fetch(`${API_BASE_URL}/api/state`)
      if (!response.ok) return
      const data: StateResponse = await response.json()
      state.feeds = data.feeds
      state.backoffActive = data.backoff_active
      onUpdate(state)
    } catch {
      // Best-effort only -- the SSE 'error' listener above already surfaces
      // a dead connection; this poll failing too isn't new information.
    }
  }
  setInterval(pollStatus, STATUS_POLL_INTERVAL_MS)
}

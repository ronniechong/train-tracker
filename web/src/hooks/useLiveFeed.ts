import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { DeltaResponse, FeedStatus, StateResponse, Train } from '../api-types'

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

const INITIAL_STATE: LiveState = {
  trains: new Map(),
  feeds: {},
  backoffActive: false,
  connection: 'connecting',
}

/** Opens the SSE stream on mount, keeps it open for the component's
 * lifetime, and closes it on unmount. `trainsRef` mirrors the latest
 * trains map outside React state so a `delta` event only needs to clone
 * once, not re-derive from the whole state object. */
export function useLiveFeed(): LiveState {
  const [state, setState] = useState<LiveState>(INITIAL_STATE)
  const trainsRef = useRef(new Map<string, Train>())

  useEffect(() => {
    const source = new EventSource(`${API_BASE_URL}/api/stream`)

    source.addEventListener('open', () => {
      setState((prev) => ({ ...prev, connection: 'live' }))
    })

    source.addEventListener('error', () => {
      // EventSource retries on its own; this only affects what the status
      // indicator honestly shows in the meantime.
      setState((prev) => ({ ...prev, connection: 'reconnecting' }))
    })

    source.addEventListener('snapshot', (event) => {
      const data: StateResponse = JSON.parse((event as MessageEvent).data)
      trainsRef.current = new Map(data.trains.map((train) => [train.trip_id, train]))
      setState({
        trains: trainsRef.current,
        feeds: data.feeds,
        backoffActive: data.backoff_active,
        connection: 'live',
      })
    })

    source.addEventListener('delta', (event) => {
      const data: DeltaResponse = JSON.parse((event as MessageEvent).data)
      const trains = new Map(trainsRef.current)
      for (const train of data.changed) {
        trains.set(train.trip_id, train)
      }
      for (const tripId of data.removed) {
        trains.delete(tripId)
      }
      trainsRef.current = trains
      setState((prev) => ({ ...prev, trains }))
    })

    async function pollStatus(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}/api/state`)
        if (!response.ok) return
        const data: StateResponse = await response.json()
        setState((prev) => ({ ...prev, feeds: data.feeds, backoffActive: data.backoff_active }))
      } catch {
        // Best-effort only -- the SSE 'error' listener above already surfaces
        // a dead connection; this poll failing too isn't new information.
      }
    }
    const intervalId = setInterval(pollStatus, STATUS_POLL_INTERVAL_MS)

    return () => {
      source.close()
      clearInterval(intervalId)
    }
  }, [])

  return state
}

import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { StationScheduleResponse } from '../api-types'

// Unlike useAttribution (static, fetch-once), this data is meant to read as
// near-real-time -- re-polled every REFRESH_INTERVAL_MS while a station
// stays selected, same order of magnitude as Trip Updates' own refresh
// cadence, comfortably inside the backend's per-IP rate limit (120 req/60s).
const REFRESH_INTERVAL_MS = 30_000

export interface StationScheduleState {
  data: StationScheduleResponse | null
  loading: boolean
  error: boolean
}

export function useStationSchedule(stationId: string | null): StationScheduleState {
  const [state, setState] = useState<StationScheduleState>({
    data: null,
    loading: false,
    error: false,
  })

  useEffect(() => {
    if (!stationId) {
      setState({ data: null, loading: false, error: false })
      return
    }

    let cancelled = false

    async function load(): Promise<void> {
      setState((prev) => ({ ...prev, loading: prev.data === null }))
      try {
        const response = await fetch(`${API_BASE_URL}/stations/${encodeURIComponent(stationId!)}/schedule`)
        if (!response.ok) {
          if (!cancelled) setState({ data: null, loading: false, error: true })
          return
        }
        const data: StationScheduleResponse = await response.json()
        if (!cancelled) setState({ data, loading: false, error: false })
      } catch {
        if (!cancelled) setState({ data: null, loading: false, error: true })
      }
    }

    load()
    const interval = setInterval(load, REFRESH_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [stationId])

  return state
}

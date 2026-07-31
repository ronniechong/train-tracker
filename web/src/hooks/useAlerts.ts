import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { Alert } from '../api-types'

// Service Alerts change on the order of the feed's own measured cadence
// (~51s avg per CLAUDE.md's SA soak-gate finding), not TU's ~10s -- polling
// this fast would just repeat the same list; 60s tracks the data's real
// rate of change without hammering the rate limiter for no benefit.
const REFRESH_INTERVAL_MS = 60_000

export interface AlertsState {
  alerts: Alert[]
  loading: boolean
  error: boolean
}

// Network-wide (no route_id filter) -- this is a general "what's currently
// disrupted" surface, not scoped to whatever station happens to be
// selected. Per-line/per-trip narrowing is 05b's job (the AI layer reads
// this same local state, more precisely, via its own tools).
export function useAlerts(): AlertsState {
  const [state, setState] = useState<AlertsState>({ alerts: [], loading: true, error: false })

  useEffect(() => {
    let cancelled = false

    async function load(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}/api/alerts`)
        if (!response.ok) {
          if (!cancelled) setState((prev) => ({ ...prev, loading: false, error: true }))
          return
        }
        const data: { alerts: Alert[] } = await response.json()
        if (!cancelled) setState({ alerts: data.alerts, loading: false, error: false })
      } catch {
        if (!cancelled) setState((prev) => ({ ...prev, loading: false, error: true }))
      }
    }

    load()
    const interval = setInterval(load, REFRESH_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  return state
}

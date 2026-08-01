import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { WeeklyDigest } from '../api-types'

export interface WeeklyDigestsState {
  digests: WeeklyDigest[]
  loading: boolean
  error: boolean
}

// Fetch once on mount, no polling interval -- unlike useAlerts (60s, tracks
// SA's own measured change cadence), this content only ever changes once a
// week (WeeklyDigestTrigger fires Monday 8am Melbourne time). A page left
// open won't see a mid-session update, but re-polling every request would
// just hammer the rate limiter for data that's static all week.
export function useWeeklyDigests(): WeeklyDigestsState {
  const [state, setState] = useState<WeeklyDigestsState>({ digests: [], loading: true, error: false })

  useEffect(() => {
    let cancelled = false

    async function load(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}/digests/weekly`)
        if (!response.ok) {
          if (!cancelled) setState((prev) => ({ ...prev, loading: false, error: true }))
          return
        }
        const data: { digests: WeeklyDigest[] } = await response.json()
        if (!cancelled) setState({ digests: data.digests, loading: false, error: false })
      } catch {
        if (!cancelled) setState((prev) => ({ ...prev, loading: false, error: true }))
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  return state
}

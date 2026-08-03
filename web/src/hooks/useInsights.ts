import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { InsightsRangeName, InsightsResponse } from '../api-types'

export interface InsightsQuery {
  range: InsightsRangeName
  // Only meaningful when range === 'custom' -- ISO date strings.
  start?: string
  end?: string
}

export interface InsightsState {
  data: InsightsResponse | null
  loading: boolean
  error: boolean
}

// Fetch-on-query-change, no polling -- unlike useLiveFeed's SSE stream,
// this is precomputed/cached server-side (locked compute-strategy
// decision), refreshed at most every 5 minutes for "today" server-side.
// Re-fetching here just means picking up whatever the server last
// computed, not triggering new computation.
export function useInsights(query: InsightsQuery): InsightsState {
  const [state, setState] = useState<InsightsState>({ data: null, loading: true, error: false })

  useEffect(() => {
    let cancelled = false
    setState((prev) => ({ ...prev, loading: true, error: false }))

    async function load(): Promise<void> {
      try {
        const params = new URLSearchParams({ range: query.range })
        if (query.range === 'custom') {
          if (query.start) params.set('start', query.start)
          if (query.end) params.set('end', query.end)
        }
        const response = await fetch(`${API_BASE_URL}/api/insights?${params.toString()}`)
        if (!response.ok) {
          if (!cancelled) setState({ data: null, loading: false, error: true })
          return
        }
        const data: InsightsResponse = await response.json()
        if (!cancelled) setState({ data, loading: false, error: false })
      } catch {
        if (!cancelled) setState({ data: null, loading: false, error: true })
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [query.range, query.start, query.end])

  return state
}

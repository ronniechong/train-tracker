import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { Attribution } from '../api-types'

// Static content (a license condition, not live data -- see the backend's
// AttributionResponse docstring) -- fetched once on mount, no polling.
export function useAttribution(): Attribution | null {
  const [attribution, setAttribution] = useState<Attribution | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}/attribution`)
        if (!response.ok) return
        const data: Attribution = await response.json()
        if (!cancelled) setAttribution(data)
      } catch {
        // Best-effort -- a missing credit line isn't worth surfacing an
        // error state over; the license text isn't behaviour-critical.
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  return attribution
}

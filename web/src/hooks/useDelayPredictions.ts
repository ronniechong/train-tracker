import { useCallback, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { DelayPredictionResponse } from '../api-types'

export type DelayPredictionState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ok'; predictedDelaySeconds: number; predictedAt: string; stale: boolean }

export interface DelayPredictions {
  byTripId: ReadonlyMap<string, DelayPredictionState>
  request: (tripId: string) => void
}

// On-demand only ("Am I late?" CTA) -- no polling, no auto-refresh. A
// click re-fetches a fresh prediction for that one trip; predictions for
// other trips are untouched. Pure client-side state, not persisted --
// gone on reload -- matching the "as of <time>, click again for an
// update" convention the tooltip shows this under. No LLM call on the
// backend side either (plain regression against already-tracked live
// state), so repeated clicks carry no per-request cost beyond ordinary
// rate limiting.
export function useDelayPredictions(): DelayPredictions {
  const [byTripId, setByTripId] = useState<Map<string, DelayPredictionState>>(() => new Map())

  const request = useCallback((tripId: string) => {
    setByTripId((prev) => new Map(prev).set(tripId, { status: 'loading' }))

    fetch(`${API_BASE_URL}/trains/${encodeURIComponent(tripId)}/delay-prediction`)
      .then(async (response) => {
        if (!response.ok) throw new Error('prediction request failed')
        const data: DelayPredictionResponse = await response.json()
        setByTripId((prev) => new Map(prev).set(tripId, {
          status: 'ok',
          predictedDelaySeconds: data.predicted_delay_seconds,
          predictedAt: data.predicted_at,
          stale: data.stale,
        }))
      })
      .catch(() => {
        setByTripId((prev) => new Map(prev).set(tripId, { status: 'error' }))
      })
  }, [])

  return { byTripId, request }
}

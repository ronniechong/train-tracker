import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../config'
import type { ArchiveStatus } from '../api-types'

// Same "best-effort, fetch once, stay quiet on failure" convention as
// useAttribution -- a missing archive-status line isn't worth an error
// state over; the fact itself isn't behaviour-critical.
export function useArchiveStatus(): ArchiveStatus | null {
  const [status, setStatus] = useState<ArchiveStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}/archive/status`)
        if (!response.ok) return
        const data: ArchiveStatus = await response.json()
        if (!cancelled) setStatus(data)
      } catch {
        // Best-effort, see module docstring.
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  return status
}

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WeeklyDigestPanel } from './WeeklyDigestPanel'

vi.mock('../../hooks/useWeeklyDigests', () => ({
  useWeeklyDigests: vi.fn(),
}))

vi.mock('../../geometry', () => ({
  routesById: new Map([
    ['r1', { id: 'r1', name: 'Belgrave', color: '#0072CE', stationIds: [], shape: [] }],
  ]),
}))

vi.mock('../../lib/formatDigest', () => ({
  formatWeekRange: () => '28 Jul – 3 Aug 2026',
  formatPercent: (n: number) => `${(n * 100).toFixed(0)}%`,
  parseNarrative: (s: string) => [{ text: s, bold: false }],
}))

import { useWeeklyDigests } from '../../hooks/useWeeklyDigests'
const mockUseWeeklyDigests = vi.mocked(useWeeklyDigests)

describe('WeeklyDigestPanel', () => {
  it('renders loading state', () => {
    mockUseWeeklyDigests.mockReturnValue({ digests: [], loading: true, error: false })
    render(<WeeklyDigestPanel />)
    expect(screen.getByText('Weekly performance')).toBeInTheDocument()
    expect(screen.getByText('Loading weekly digest…')).toBeInTheDocument()
  })

  it('renders empty state', () => {
    mockUseWeeklyDigests.mockReturnValue({ digests: [], loading: false, error: false })
    render(<WeeklyDigestPanel />)
    expect(screen.getByText('Weekly performance')).toBeInTheDocument()
    expect(screen.getByText(/No weekly digest yet/)).toBeInTheDocument()
  })

  it('renders digest when present', () => {
    mockUseWeeklyDigests.mockReturnValue({
      digests: [
        {
          week_start: '2026-07-28',
          week_end: '2026-08-03',
          on_time_pct: 0.92,
          on_time_count: 92,
          late_count: 8,
          days_covered: 7,
          narrative: 'Good week for Belgrave line.',
          line_stats: [],
        },
      ],
      loading: false,
      error: false,
    })
    render(<WeeklyDigestPanel />)
    expect(screen.getByText('Weekly performance')).toBeInTheDocument()
    expect(screen.getByText('28 Jul – 3 Aug 2026')).toBeInTheDocument()
  })

  it('renders line stats', () => {
    mockUseWeeklyDigests.mockReturnValue({
      digests: [
        {
          week_start: '2026-07-28',
          week_end: '2026-08-03',
          on_time_pct: 0.92,
          on_time_count: 92,
          late_count: 8,
          days_covered: 7,
          narrative: null,
          line_stats: [
            { route_id: 'r1', on_time_pct: 0.92, total_trips: 100, cancelled_trips: 2 },
          ],
        },
      ],
      loading: false,
      error: false,
    })
    render(<WeeklyDigestPanel />)
    expect(screen.getByText('Belgrave')).toBeInTheDocument()
    expect(screen.getByText('92%')).toBeInTheDocument()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StatusPanel } from './StatusPanel'
import type { LiveState } from '../../hooks/useLiveFeed'

function createLiveState(overrides: Partial<LiveState> = {}): LiveState {
  return {
    trains: new Map(),
    feeds: {
      'vehicle-positions': { last_changed_at: new Date().toISOString(), stale: false },
      'trip-updates': { last_changed_at: new Date().toISOString(), stale: false },
    },
    backoffActive: false,
    connection: 'live',
    ...overrides,
  }
}

describe('StatusPanel', () => {
  it('renders section title', () => {
    render(
      <StatusPanel liveState={createLiveState()} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText('Data status')).toBeInTheDocument()
  })

  it('shows Live when connected', () => {
    render(
      <StatusPanel liveState={createLiveState({ connection: 'live' })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    // Use the connection paragraph, not the tooltip
    const liveElements = screen.getAllByText('Live')
    expect(liveElements.length).toBeGreaterThan(0)
  })

  it('shows Connecting when connecting', () => {
    render(
      <StatusPanel liveState={createLiveState({ connection: 'connecting' })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText('Connecting…')).toBeInTheDocument()
  })

  it('shows Reconnecting when reconnecting', () => {
    render(
      <StatusPanel liveState={createLiveState({ connection: 'reconnecting' })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText('Reconnecting…')).toBeInTheDocument()
  })

  it('shows backoff warning when active', () => {
    render(
      <StatusPanel liveState={createLiveState({ backoffActive: true })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText(/Backoff active/)).toBeInTheDocument()
  })

  it('does not show backoff warning when inactive', () => {
    render(
      <StatusPanel liveState={createLiveState({ backoffActive: false })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.queryByText(/Backoff active/)).not.toBeInTheDocument()
  })

  it('shows feed names', () => {
    render(
      <StatusPanel liveState={createLiveState()} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText('Vehicle Positions')).toBeInTheDocument()
    expect(screen.getByText('Trip Updates')).toBeInTheDocument()
  })

  it('shows train counts', () => {
    const trains = new Map([
      ['t1', { trip_id: 't1', route_id: 'r1', status: 'live' as const, latitude: 0, longitude: 0, bearing: 0, position_updated_at: null, schedule_updated_at: null, last_seen_at: null, start_time: null, trip_headsign: null, direction_id: null, next_stop_id: null, next_stop_name: null, next_stop_delay_seconds: null, progress_stop_sequence: null, progress_total_stops: null }],
      ['t2', { trip_id: 't2', route_id: 'r2', status: 'coasting' as const, latitude: 0, longitude: 0, bearing: 0, position_updated_at: null, schedule_updated_at: null, last_seen_at: null, start_time: null, trip_headsign: null, direction_id: null, next_stop_id: null, next_stop_name: null, next_stop_delay_seconds: null, progress_stop_sequence: null, progress_total_stops: null }],
      ['t3', { trip_id: 't3', route_id: 'r3', status: 'ghost' as const, latitude: 0, longitude: 0, bearing: 0, position_updated_at: null, schedule_updated_at: null, last_seen_at: null, start_time: null, trip_headsign: null, direction_id: null, next_stop_id: null, next_stop_name: null, next_stop_delay_seconds: null, progress_stop_sequence: null, progress_total_stops: null }],
    ])
    render(
      <StatusPanel liveState={createLiveState({ trains })} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByText('1 live')).toBeInTheDocument()
    expect(screen.getByText('1 coasting')).toBeInTheDocument()
    expect(screen.getByText('1 ghost')).toBeInTheDocument()
  })

  it('renders ghost toggle', () => {
    render(
      <StatusPanel liveState={createLiveState()} hideGhosts={false} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByRole('checkbox', { name: 'Hide ghost trains' })).toBeInTheDocument()
  })

  it('ghost toggle reflects hideGhosts prop', () => {
    render(
      <StatusPanel liveState={createLiveState()} hideGhosts={true} onToggleHideGhosts={() => {}} />
    )
    expect(screen.getByRole('checkbox', { name: 'Hide ghost trains' })).toBeChecked()
  })

  it('calls onToggleHideGhosts when toggling', async () => {
    const user = userEvent.setup()
    const onToggleHideGhosts = vi.fn()
    render(
      <StatusPanel liveState={createLiveState()} hideGhosts={false} onToggleHideGhosts={onToggleHideGhosts} />
    )

    await user.click(screen.getByRole('checkbox', { name: 'Hide ghost trains' }))
    expect(onToggleHideGhosts).toHaveBeenCalledWith(true)
  })
})

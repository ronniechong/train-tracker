import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StationPanel } from './StationPanel'

vi.mock('../../geometry', () => ({
  stationsById: new Map([
    ['s1', { id: 's1', name: 'Flinders Street', lat: -37.8183, lon: 144.967 }],
  ]),
  routesById: new Map([
    ['r1', { id: 'r1', name: 'Belgrave', color: '#0072CE' }],
  ]),
}))

vi.mock('../../map/trainMarkers', () => ({
  lineNameForTrain: () => 'Belgrave',
  markerColor: () => '#0072CE',
  STATUS_LABEL: { live: 'Live', coasting: 'Coasting', ghost: 'Ghost' },
}))

vi.mock('../../lib/relativeTime', () => ({
  relativeTime: () => '2 min ago',
}))

vi.mock('../../lib/formatTime', () => ({
  formatTime: () => '10:30 AM',
}))

vi.mock('../../lib/geo', () => ({
  haversineM: () => 500,
}))

// Use real Section component since it's simple
vi.mock('../Section/Section', () => ({
  Section: ({ children, title, ...props }: { children: React.ReactNode; title?: string; [key: string]: unknown }) => (
    <section {...props}>
      {title && <p>{title}</p>}
      {children}
    </section>
  ),
  Placeholder: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}))

describe('StationPanel', () => {
  it('renders station name', () => {
    render(
      <StationPanel
        stationId="s1"
        trains={new Map()}
        hideGhosts={false}
        schedule={{ data: null, loading: false, error: false }}
        onClear={() => {}}
      />
    )
    expect(screen.getByText('Flinders Street')).toBeInTheDocument()
  })

  it('renders close button', () => {
    render(
      <StationPanel
        stationId="s1"
        trains={new Map()}
        hideGhosts={false}
        schedule={{ data: null, loading: false, error: false }}
        onClear={() => {}}
      />
    )
    expect(screen.getByRole('button', { name: /clear selected station/i })).toBeInTheDocument()
  })

  it('calls onClear when close button clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    const onClear = vi.fn()
    render(
      <StationPanel
        stationId="s1"
        trains={new Map()}
        hideGhosts={false}
        schedule={{ data: null, loading: false, error: false }}
        onClear={onClear}
      />
    )
    // Click the close button - use the × text content
    const closeButton = screen.getByText('×')
    await user.click(closeButton)
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('renders nearby trains', () => {
    const trains = new Map([
      ['t1', {
        trip_id: 't1',
        route_id: 'r1',
        status: 'live' as const,
        latitude: -37.818,
        longitude: 144.967,
        bearing: 90,
        position_updated_at: new Date().toISOString(),
        schedule_updated_at: null,
        last_seen_at: new Date().toISOString(),
        start_time: null,
        trip_headsign: null,
        direction_id: null,
        next_stop_id: null,
        next_stop_name: null,
        next_stop_delay_seconds: null,
      }],
    ])
    render(
      <StationPanel
        stationId="s1"
        trains={trains}
        hideGhosts={false}
        schedule={{ data: null, loading: false, error: false }}
        onClear={() => {}}
      />
    )
    expect(screen.getByText(/Belgrave/)).toBeInTheDocument()
  })

  it('renders schedule departures', () => {
    const schedule = {
      data: {
        station_id: 's1',
        generated_at: new Date().toISOString(),
        wheelchair_boarding: null,
        departures: [
          {
            trip_id: 't1',
            route_id: 'r1',
            direction_id: 0,
            headsign: 'Belgrave',
            scheduled_time: new Date().toISOString(),
            predicted_time: null,
            delay_seconds: null,
            is_live: false,
            is_cancelled: false,
            is_added: false,
            platform_code: null,
          },
        ],
        lines_no_service_today: [],
      },
      loading: false,
      error: false,
    }
    render(
      <StationPanel
        stationId="s1"
        trains={new Map()}
        hideGhosts={false}
        schedule={schedule}
        onClear={() => {}}
      />
    )
    expect(screen.getByText('Belgrave')).toBeInTheDocument()
  })

  it('shows lines with no service today', () => {
    const schedule = {
      data: {
        station_id: 's1',
        generated_at: new Date().toISOString(),
        wheelchair_boarding: null,
        departures: [],
        lines_no_service_today: [{ route_id: 'r1', short_name: 'BEG', long_name: 'Belgrave' }],
      },
      loading: false,
      error: false,
    }
    render(
      <StationPanel
        stationId="s1"
        trains={new Map()}
        hideGhosts={false}
        schedule={schedule}
        onClear={() => {}}
      />
    )
    expect(screen.getByText(/No service today on Belgrave/)).toBeInTheDocument()
  })
})

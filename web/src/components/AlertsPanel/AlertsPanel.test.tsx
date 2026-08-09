import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AlertsPanel } from './AlertsPanel'

describe('AlertsPanel', () => {
  it('shows a loading placeholder while loading', () => {
    render(<AlertsPanel alerts={[]} loading={true} error={false} />)
    expect(screen.getByText('Loading alerts…')).toBeInTheDocument()
  })

  it('renders nothing on error', () => {
    const { container } = render(<AlertsPanel alerts={[]} loading={false} error={true} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows an explicit empty state when there are no active alerts', () => {
    render(<AlertsPanel alerts={[]} loading={false} error={false} />)
    expect(screen.getByText('No active service alerts')).toBeInTheDocument()
  })

  it('does not render its own title -- the tab label owns that now', () => {
    render(<AlertsPanel alerts={[]} loading={false} error={false} />)
    expect(screen.queryByText(/^Service alerts/)).not.toBeInTheDocument()
  })

  it('renders alerts when present', () => {
    render(
      <AlertsPanel
        alerts={[
          { id: '1', effect: 'NO_SERVICE', header_text: 'No trains on Belgrave line', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        ]}
        loading={false}
        error={false}
      />,
    )
    expect(screen.getByText('No service')).toBeInTheDocument()
    expect(screen.getByText('No trains on Belgrave line')).toBeInTheDocument()
  })

  it('falls back to "Disruption" for unknown effect', () => {
    render(
      <AlertsPanel
        alerts={[
          { id: '1', effect: 'UNKNOWN_EFFECT', header_text: 'Something happened', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        ]}
        loading={false}
        error={false}
      />,
    )
    expect(screen.getByText('Disruption')).toBeInTheDocument()
  })

  it('shows "Service alert" when header_text is null', () => {
    render(
      <AlertsPanel
        alerts={[
          { id: '1', effect: 'DETOUR', header_text: null, cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        ]}
        loading={false}
        error={false}
      />,
    )
    expect(screen.getByText('Service alert')).toBeInTheDocument()
  })

  it('shows resolved line name and since-time when available', () => {
    render(
      <AlertsPanel
        alerts={[
          {
            id: '1',
            effect: 'NO_SERVICE',
            header_text: 'Buses replace trains',
            cause: null,
            description_text: null,
            url: null,
            active_periods: [{ start: '2026-08-04T06:00:00Z', end: null }],
            informed_entities: [
              { route_id: '2-PKM', route_name: 'Pakenham - City', stop_id: null, direction_id: null },
            ],
          },
        ]}
        loading={false}
        error={false}
      />,
    )
    expect(screen.getByText(/Pakenham - City/)).toBeInTheDocument()
    expect(screen.getByText(/since/)).toBeInTheDocument()
  })

  it('omits the meta line when no line name or timestamp resolved', () => {
    render(
      <AlertsPanel
        alerts={[
          { id: '1', effect: 'NO_SERVICE', header_text: 'Alert', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        ]}
        loading={false}
        error={false}
      />,
    )
    expect(screen.queryByText(/since/)).not.toBeInTheDocument()
  })
})

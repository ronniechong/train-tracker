import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AlertsPanel } from './AlertsPanel'

vi.mock('../../hooks/useAlerts', () => ({
  useAlerts: vi.fn(),
}))

import { useAlerts } from '../../hooks/useAlerts'
const mockUseAlerts = vi.mocked(useAlerts)

describe('AlertsPanel', () => {
  it('renders nothing when loading', () => {
    mockUseAlerts.mockReturnValue({ alerts: [], loading: true, error: false })
    const { container } = render(<AlertsPanel />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing on error', () => {
    mockUseAlerts.mockReturnValue({ alerts: [], loading: false, error: true })
    const { container } = render(<AlertsPanel />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when no alerts', () => {
    mockUseAlerts.mockReturnValue({ alerts: [], loading: false, error: false })
    const { container } = render(<AlertsPanel />)
    expect(container.firstChild).toBeNull()
  })

  it('renders alerts when present', () => {
    mockUseAlerts.mockReturnValue({
      alerts: [
        { id: '1', effect: 'NO_SERVICE', header_text: 'No trains on Belgrave line', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
      ],
      loading: false,
      error: false,
    })
    render(<AlertsPanel />)
    expect(screen.getByText('Service alerts (1)')).toBeInTheDocument()
    expect(screen.getByText('No service')).toBeInTheDocument()
    expect(screen.getByText('No trains on Belgrave line')).toBeInTheDocument()
  })

  it('renders multiple alerts', () => {
    mockUseAlerts.mockReturnValue({
      alerts: [
        { id: '1', effect: 'NO_SERVICE', header_text: 'Alert 1', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        { id: '2', effect: 'SIGNIFICANT_DELAYS', header_text: 'Alert 2', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
      ],
      loading: false,
      error: false,
    })
    render(<AlertsPanel />)
    expect(screen.getByText('Service alerts (2)')).toBeInTheDocument()
  })

  it('falls back to "Disruption" for unknown effect', () => {
    mockUseAlerts.mockReturnValue({
      alerts: [
        { id: '1', effect: 'UNKNOWN_EFFECT', header_text: 'Something happened', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
      ],
      loading: false,
      error: false,
    })
    render(<AlertsPanel />)
    expect(screen.getByText('Disruption')).toBeInTheDocument()
  })

  it('shows "Service alert" when header_text is null', () => {
    mockUseAlerts.mockReturnValue({
      alerts: [
        { id: '1', effect: 'DETOUR', header_text: null, cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
      ],
      loading: false,
      error: false,
    })
    render(<AlertsPanel />)
    expect(screen.getByText('Service alert')).toBeInTheDocument()
  })
})

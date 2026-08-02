import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { App } from './App'

// Mock all hooks
vi.mock('./hooks/useLiveFeed', () => ({
  useLiveFeed: vi.fn(() => ({
    trains: new Map(),
    feeds: {},
    backoffActive: false,
    connection: 'live',
  })),
}))

vi.mock('./hooks/useStationSchedule', () => ({
  useStationSchedule: vi.fn(() => null),
}))

vi.mock('./hooks/useTheme', () => ({
  useTheme: vi.fn(() => ['light', vi.fn()]),
}))

vi.mock('./geometry', () => ({
  geometry: { stations: [], routes: [], bounds: { west: 0, east: 0, south: 0, north: 0 } },
  stationsById: new Map(),
  routesByStationId: new Map(),
  routesById: new Map(),
}))

// Mock maplibre-gl
vi.mock('maplibre-gl', () => ({
  default: {
    Map: vi.fn(() => ({
      on: vi.fn(),
      addSource: vi.fn(),
      addLayer: vi.fn(),
      flyTo: vi.fn(),
      fitBounds: vi.fn(),
      remove: vi.fn(),
    })),
    NavigationControl: vi.fn(),
    addProtocol: vi.fn(),
  },
}))

// Mock child components to avoid their complex dependencies
vi.mock('./components/Sidebar/Sidebar', () => ({
  Sidebar: vi.fn(({ open }: { open: boolean }) => <div data-testid="sidebar" data-open={open}>Sidebar</div>),
}))

vi.mock('./components/MapView/MapView', () => ({
  MapView: vi.fn(() => <div data-testid="map">Map</div>),
}))

vi.mock('./components/DrawerToggle/DrawerToggle', () => ({
  DrawerToggle: vi.fn(({ open, onToggle }: { open: boolean; onToggle: () => void }) => (
    <button data-testid="drawer-toggle" onClick={onToggle}>
      {open ? 'Close' : 'Open'}
    </button>
  )),
}))

describe('App', () => {
  it('renders without crashing', () => {
    render(<App />)
    expect(screen.getByTestId('sidebar')).toBeInTheDocument()
    expect(screen.getByTestId('map')).toBeInTheDocument()
  })

  it('renders drawer toggle', () => {
    render(<App />)
    expect(screen.getByTestId('drawer-toggle')).toBeInTheDocument()
  })

  it('toggles drawer when button clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    render(<App />)

    const toggle = screen.getByTestId('drawer-toggle')
    expect(toggle).toHaveTextContent('Open')

    await user.click(toggle)
    expect(toggle).toHaveTextContent('Close')
  })
})

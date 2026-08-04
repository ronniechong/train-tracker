import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Sidebar } from './Sidebar'
import type { LiveState } from '../../hooks/useLiveFeed'

// Mock hooks
vi.mock('../../hooks/useAttribution', () => ({
  useAttribution: vi.fn(() => ({
    attribution: { source: 'PTV', license: 'CC BY 4.0', license_url: '', note: '' },
    loading: false,
  })),
}))

// Mock child components
vi.mock('../Header/Header', () => ({
  Header: vi.fn(() => <div data-testid="header">Header</div>),
}))

vi.mock('../Legend/Legend', () => ({
  Legend: vi.fn(() => <div data-testid="legend">Legend</div>),
}))

vi.mock('../Search/Search', () => ({
  Search: vi.fn(() => <div data-testid="search">Search</div>),
}))

vi.mock('../StatusPanel/StatusPanel', () => ({
  StatusPanel: vi.fn(() => <div data-testid="status-panel">StatusPanel</div>),
}))

vi.mock('../StationPanel/StationPanel', () => ({
  StationPanel: vi.fn(() => <div data-testid="station-panel">StationPanel</div>),
}))

vi.mock('../AlertsPanel/AlertsPanel', () => ({
  AlertsPanel: vi.fn(() => <div data-testid="alerts-panel">AlertsPanel</div>),
}))

vi.mock('../WeeklyDigestPanel/WeeklyDigestPanel', () => ({
  WeeklyDigestPanel: vi.fn(() => <div data-testid="weekly-digest-panel">WeeklyDigestPanel</div>),
}))

vi.mock('../Modal/Modal', () => ({
  Modal: vi.fn(({ children }: { children: React.ReactNode }) => <div data-testid="modal">{children}</div>),
}))

vi.mock('../Section/Section', () => ({
  Section: vi.fn(({ children, title }: { children: React.ReactNode; title?: string }) => (
    <div data-testid="section">
      {title && <p>{title}</p>}
      {children}
    </div>
  )),
  Placeholder: vi.fn(({ children }: { children: React.ReactNode }) => <p>{children}</p>),
}))

// Sidebar renders outside a FlagsmithProvider in these tests -- mock the
// flag as off, same as Production's real default, so the (flag-gated)
// Insights <Link> never renders and doesn't need a Router context either.
vi.mock('@flagsmith/flagsmith/react', () => ({
  useFlags: vi.fn(() => ({ 'train-insghts': { enabled: false, value: null } })),
}))

function createLiveState(overrides: Partial<LiveState> = {}): LiveState {
  return {
    trains: new Map(),
    feeds: {},
    backoffActive: false,
    connection: 'live',
    ...overrides,
  }
}

describe('Sidebar', () => {
  const defaultProps = {
    liveState: createLiveState(),
    hiddenRouteIds: new Set<string>(),
    onToggleRoute: vi.fn(),
    hideGhosts: false,
    onToggleHideGhosts: vi.fn(),
    onSearchSelect: vi.fn(),
    selectedStationId: null,
    onClearStation: vi.fn(),
    onRecenter: vi.fn(),
    onCloseDrawer: vi.fn(),
    open: false,
    theme: 'light' as const,
    onThemeChange: vi.fn(),
    schedule: { data: null, loading: false, error: false },
  }

  it('renders header', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByTestId('header')).toBeInTheDocument()
  })

  it('renders legend section', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByTestId('legend')).toBeInTheDocument()
  })

  it('renders search section', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByTestId('search')).toBeInTheDocument()
  })

  it('renders data status section', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByTestId('status-panel')).toBeInTheDocument()
  })

  it('applies open class when open', () => {
    const { container } = render(<Sidebar {...defaultProps} open={true} />)
    const sidebar = container.firstChild as HTMLElement
    expect(sidebar.className).toContain('open')
  })

  it('does not apply open class when closed', () => {
    const { container } = render(<Sidebar {...defaultProps} open={false} />)
    const sidebar = container.firstChild as HTMLElement
    expect(sidebar.className).not.toContain('open')
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from './Sidebar'
import { useAlerts } from '../../hooks/useAlerts'
import { useArchiveStatus } from '../../hooks/useArchiveStatus'
import type { LiveState } from '../../hooks/useLiveFeed'

const mockUseAlerts = vi.mocked(useAlerts)
const mockUseArchiveStatus = vi.mocked(useArchiveStatus)

// Mock hooks
vi.mock('../../hooks/useAttribution', () => ({
  useAttribution: vi.fn(() => ({
    attribution: { source: 'PTV', license: 'CC BY 4.0', license_url: '', note: '' },
    loading: false,
  })),
}))

vi.mock('../../hooks/useAlerts', () => ({
  useAlerts: vi.fn(() => ({ alerts: [], loading: false, error: false })),
}))

vi.mock('../../hooks/useArchiveStatus', () => ({
  useArchiveStatus: vi.fn(() => null),
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

  it('shows the last archived date when the archiver has run', () => {
    mockUseArchiveStatus.mockReturnValue({ last_archived_date: '2026-08-13' })
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByText(/Last archived day: Aug 13, 2026/)).toBeInTheDocument()
    mockUseArchiveStatus.mockReturnValue(null)
  })

  it('shows nothing about the archive when the archiver has never run', () => {
    mockUseArchiveStatus.mockReturnValue({ last_archived_date: null })
    render(<Sidebar {...defaultProps} />)
    expect(screen.queryByText(/Last archived day/)).not.toBeInTheDocument()
    mockUseArchiveStatus.mockReturnValue(null)
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

  it('opens the Announcements modal on Weekly performance by default', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...defaultProps} />)

    await user.click(screen.getByRole('button', { name: 'Announcements' }))

    expect(screen.getByTestId('modal')).toBeInTheDocument()
    expect(screen.getByTestId('weekly-digest-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('alerts-panel')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Weekly performance' })).toHaveAttribute('aria-selected', 'true')
  })

  it('switches to Service alerts when that tab is clicked', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...defaultProps} />)

    await user.click(screen.getByRole('button', { name: 'Announcements' }))
    await user.click(screen.getByRole('tab', { name: 'Service alerts' }))

    expect(screen.getByTestId('alerts-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('weekly-digest-panel')).not.toBeInTheDocument()
  })

  it('shows the alert count in the tab label as soon as the modal opens', async () => {
    mockUseAlerts.mockReturnValue({
      alerts: [
        { id: '1', effect: 'NO_SERVICE', header_text: 'Alert 1', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
        { id: '2', effect: 'DETOUR', header_text: 'Alert 2', cause: null, description_text: null, url: null, active_periods: [], informed_entities: [] },
      ],
      loading: false,
      error: false,
    })
    const user = userEvent.setup()
    render(<Sidebar {...defaultProps} />)

    await user.click(screen.getByRole('button', { name: 'Announcements' }))

    // Count shows up on the tab label without needing to switch to it --
    // AlertsPanel itself is still on the (default) Weekly performance tab.
    expect(screen.getByRole('tab', { name: 'Service alerts (2)' })).toBeInTheDocument()
    expect(screen.queryByTestId('alerts-panel')).not.toBeInTheDocument()
  })
})

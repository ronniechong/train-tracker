import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Legend } from './Legend'

// Mock geometry data - routes will be sorted by LEGEND_ORDER
vi.mock('../../geometry', () => ({
  geometry: {
    routes: [
      { id: '3', name: 'Frankston', color: '#E4002B', stationIds: ['s3'], shape: [] },
      { id: '1', name: 'Belgrave', color: '#0072CE', stationIds: ['s1'], shape: [] },
      { id: '2', name: 'Lilydale', color: '#0072CE', stationIds: ['s2'], shape: [] },
    ],
    stations: [],
    bounds: { west: 0, east: 0, south: 0, north: 0 },
  },
}))

describe('Legend', () => {
  it('renders section title', () => {
    render(<Legend hiddenRouteIds={new Set()} onToggle={() => {}} />)
    expect(screen.getByText('Lines')).toBeInTheDocument()
  })

  it('renders all routes', () => {
    render(<Legend hiddenRouteIds={new Set()} onToggle={() => {}} />)
    expect(screen.getByText('Belgrave')).toBeInTheDocument()
    expect(screen.getByText('Lilydale')).toBeInTheDocument()
    expect(screen.getByText('Frankston')).toBeInTheDocument()
  })

  it('renders color swatches', () => {
    const { container } = render(<Legend hiddenRouteIds={new Set()} onToggle={() => {}} />)
    const swatches = container.querySelectorAll('[class*="swatch"]')
    expect(swatches.length).toBe(3)
  })

  it('renders toggle for each route', () => {
    render(<Legend hiddenRouteIds={new Set()} onToggle={() => {}} />)
    const toggles = screen.getAllByRole('checkbox')
    expect(toggles.length).toBe(3)
  })

  it('all toggles checked when no routes hidden', () => {
    render(<Legend hiddenRouteIds={new Set()} onToggle={() => {}} />)
    const toggles = screen.getAllByRole('checkbox')
    toggles.forEach((toggle) => {
      expect(toggle).toBeChecked()
    })
  })

  it('hides specific route when in hiddenRouteIds', () => {
    render(<Legend hiddenRouteIds={new Set(['1'])} onToggle={() => {}} />)
    // Find the Belgrave toggle by its label
    const belgraveToggle = screen.getByRole('checkbox', { name: 'Show Belgrave line' })
    expect(belgraveToggle).not.toBeChecked()
  })

  it('calls onToggle when clicking a toggle', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<Legend hiddenRouteIds={new Set()} onToggle={onToggle} />)

    // Click the Belgrave toggle
    const belgraveToggle = screen.getByRole('checkbox', { name: 'Show Belgrave line' })
    await user.click(belgraveToggle)

    expect(onToggle).toHaveBeenCalledWith('1', false)
  })

  it('calls onToggle with true when unhiding', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<Legend hiddenRouteIds={new Set(['1'])} onToggle={onToggle} />)

    const belgraveToggle = screen.getByRole('checkbox', { name: 'Show Belgrave line' })
    await user.click(belgraveToggle)

    expect(onToggle).toHaveBeenCalledWith('1', true)
  })
})

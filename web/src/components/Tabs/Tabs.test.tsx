import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Tabs } from './Tabs'

const TABS = [
  { id: 'weekly', label: 'Weekly performance' },
  { id: 'alerts', label: 'Service alerts' },
]

describe('Tabs', () => {
  it('renders a tab button per entry', () => {
    render(<Tabs tabs={TABS} activeId="weekly" onChange={() => {}} />)
    expect(screen.getByRole('tab', { name: 'Weekly performance' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Service alerts' })).toBeInTheDocument()
  })

  it('marks the active tab as selected', () => {
    render(<Tabs tabs={TABS} activeId="alerts" onChange={() => {}} />)
    expect(screen.getByRole('tab', { name: 'Service alerts' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Weekly performance' })).toHaveAttribute('aria-selected', 'false')
  })

  it('calls onChange with the clicked tab id', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Tabs tabs={TABS} activeId="weekly" onChange={onChange} />)

    await user.click(screen.getByRole('tab', { name: 'Service alerts' }))

    expect(onChange).toHaveBeenCalledWith('alerts')
  })

  it('does not call onChange when clicking the already-active tab', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Tabs tabs={TABS} activeId="weekly" onChange={onChange} />)

    await user.click(screen.getByRole('tab', { name: 'Weekly performance' }))

    expect(onChange).not.toHaveBeenCalled()
  })
})

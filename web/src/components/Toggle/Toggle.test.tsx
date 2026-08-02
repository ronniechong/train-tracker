import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Toggle } from './Toggle'

describe('Toggle', () => {
  it('renders a checkbox', () => {
    render(<Toggle checked={false} onChange={() => {}} />)
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  it('reflects checked state', () => {
    render(<Toggle checked={true} onChange={() => {}} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('reflects unchecked state', () => {
    render(<Toggle checked={false} onChange={() => {}} />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('calls onChange with true when toggled on', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Toggle checked={false} onChange={onChange} />)

    await user.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('calls onChange with false when toggled off', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Toggle checked={true} onChange={onChange} />)

    await user.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('applies aria-label', () => {
    render(<Toggle checked={false} onChange={() => {}} aria-label="Toggle dark mode" />)
    expect(screen.getByRole('checkbox')).toHaveAccessibleName('Toggle dark mode')
  })

  it('renders icon when provided', () => {
    render(
      <Toggle checked={false} onChange={() => {}} icon={<span data-testid="icon">🌙</span>} />
    )
    expect(screen.getByTestId('icon')).toBeInTheDocument()
  })
})

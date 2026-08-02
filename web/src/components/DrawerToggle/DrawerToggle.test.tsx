import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DrawerToggle } from './DrawerToggle'

describe('DrawerToggle', () => {
  it('renders hamburger icon when closed', () => {
    render(<DrawerToggle open={false} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveTextContent('☰')
  })

  it('renders close icon when open', () => {
    render(<DrawerToggle open={true} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveTextContent('×')
  })

  it('calls onToggle when clicked', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<DrawerToggle open={false} onToggle={onToggle} />)

    await user.click(screen.getByRole('button'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('has correct aria-label when closed', () => {
    render(<DrawerToggle open={false} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveAccessibleName('Open menu')
  })

  it('has correct aria-label when open', () => {
    render(<DrawerToggle open={true} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveAccessibleName('Close menu')
  })

  it('has aria-expanded attribute', () => {
    render(<DrawerToggle open={false} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false')
  })

  it('has aria-expanded true when open', () => {
    render(<DrawerToggle open={true} onToggle={() => {}} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  })
})

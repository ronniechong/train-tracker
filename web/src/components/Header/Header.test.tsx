import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Header } from './Header'

describe('Header', () => {
  it('renders title', () => {
    render(<Header theme="light" onThemeChange={() => {}} />)
    expect(screen.getByText('Melbourne Train Tracker')).toBeInTheDocument()
  })

  it('renders tagline', () => {
    render(<Header theme="light" onThemeChange={() => {}} />)
    expect(screen.getByText(/Tracking trains/)).toBeInTheDocument()
  })

  it('renders favicon', () => {
    render(<Header theme="light" onThemeChange={() => {}} />)
    const img = document.querySelector('img')
    expect(img).toHaveAttribute('src', './favicon.svg')
  })

  it('shows sun icon in light mode', () => {
    render(<Header theme="light" onThemeChange={() => {}} />)
    expect(screen.getByText('☀️')).toBeInTheDocument()
  })

  it('shows moon icon in dark mode', () => {
    render(<Header theme="dark" onThemeChange={() => {}} />)
    expect(screen.getByText('🌙')).toBeInTheDocument()
  })

  it('calls onThemeChange with dark when toggling on', async () => {
    const user = userEvent.setup()
    const onThemeChange = vi.fn()
    render(<Header theme="light" onThemeChange={onThemeChange} />)

    await user.click(screen.getByRole('checkbox'))
    expect(onThemeChange).toHaveBeenCalledWith('dark')
  })

  it('calls onThemeChange with light when toggling off', async () => {
    const user = userEvent.setup()
    const onThemeChange = vi.fn()
    render(<Header theme="dark" onThemeChange={onThemeChange} />)

    await user.click(screen.getByRole('checkbox'))
    expect(onThemeChange).toHaveBeenCalledWith('light')
  })
})

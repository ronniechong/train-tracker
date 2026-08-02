import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LoadingOverlay } from './LoadingOverlay'

describe('LoadingOverlay', () => {
  it('renders loading text', () => {
    render(<LoadingOverlay visible={true} />)
    expect(screen.getByText('Loading live map…')).toBeInTheDocument()
  })

  it('renders SVG spinner', () => {
    const { container } = render(<LoadingOverlay visible={true} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('applies hidden class when not visible', () => {
    const { container } = render(<LoadingOverlay visible={false} />)
    const overlay = container.firstChild as HTMLElement
    expect(overlay.className).toContain('hidden')
  })

  it('does not apply hidden class when visible', () => {
    const { container } = render(<LoadingOverlay visible={true} />)
    const overlay = container.firstChild as HTMLElement
    expect(overlay.className).not.toContain('hidden')
  })
})

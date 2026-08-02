import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Section, Placeholder } from './Section'

describe('Section', () => {
  it('renders children', () => {
    render(<Section>Content</Section>)
    expect(screen.getByText('Content')).toBeInTheDocument()
  })

  it('renders title when provided', () => {
    render(<Section title="My Section">Content</Section>)
    expect(screen.getByText('My Section')).toBeInTheDocument()
  })

  it('does not render title when not provided', () => {
    render(<Section>Content</Section>)
    expect(screen.queryByRole('paragraph')).not.toBeInTheDocument()
  })

  it('renders as section element by default', () => {
    render(<Section data-testid="section">Content</Section>)
    expect(screen.getByTestId('section').tagName).toBe('SECTION')
  })

  it('renders as custom element when as prop is set', () => {
    render(<Section as="footer" data-testid="section">Content</Section>)
    expect(screen.getByTestId('section').tagName).toBe('FOOTER')
  })

  it('spreads additional HTML attributes', () => {
    render(<Section id="custom-id" className="custom-class">Content</Section>)
    const section = document.getElementById('custom-id')
    expect(section).toBeInTheDocument()
    expect(section).toHaveClass('custom-class')
  })
})

describe('Placeholder', () => {
  it('renders children', () => {
    render(<Placeholder>Loading...</Placeholder>)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders as paragraph', () => {
    render(<Placeholder>Text</Placeholder>)
    expect(screen.getByText('Text').tagName).toBe('P')
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Modal } from './Modal'

describe('Modal', () => {
  it('renders title', () => {
    render(
      <Modal title="Test Modal" onClose={() => {}}>
        Content
      </Modal>
    )
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Test Modal')
  })

  it('renders children inside dialog', () => {
    render(
      <Modal title="Test" onClose={() => {}}>
        <p>Modal content</p>
      </Modal>
    )
    expect(screen.getByText('Modal content')).toBeInTheDocument()
  })

  it('calls onClose when close button clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <Modal title="Test" onClose={onClose}>
        Content
      </Modal>
    )

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when Escape pressed', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <Modal title="Test" onClose={onClose}>
        Content
      </Modal>
    )

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when backdrop clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <Modal title="Test" onClose={onClose}>
        Content
      </Modal>
    )

    // Click the backdrop (the outer div)
    const backdrop = screen.getByRole('dialog').parentElement!
    await user.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not call onClose when panel content clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <Modal title="Test" onClose={onClose}>
        <p>Content</p>
      </Modal>
    )

    await user.click(screen.getByText('Content'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('has aria-modal attribute', () => {
    render(
      <Modal title="Test" onClose={() => {}}>
        Content
      </Modal>
    )
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Search } from './Search'

describe('Search', () => {
  it('renders search input', () => {
    render(<Search onSelect={() => {}} />)
    expect(screen.getByRole('combobox')).toBeInTheDocument()
  })

  it('renders placeholder text', () => {
    render(<Search onSelect={() => {}} />)
    expect(screen.getByPlaceholderText('Jump to a station…')).toBeInTheDocument()
  })

  it('shows no results for empty query', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.click(input)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('shows results when typing a station name', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flinders')

    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getByText(/Flinders Street/)).toBeInTheDocument()
  })

  it('shows "No matching station" for unknown query', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'zzzzz')

    expect(screen.getByText('No matching station')).toBeInTheDocument()
  })

  it('calls onSelect when clicking a result', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<Search onSelect={onSelect} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flinders')

    const option = screen.getByText(/Flinders Street/)
    await user.click(option)

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ name: expect.stringContaining('Flinders') })
    )
  })

  it('clears input after selection', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flinders')
    await user.click(screen.getByText(/Flinders Street/))

    expect(input).toHaveValue('')
  })

  it('navigates results with arrow keys', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flin')

    // First result should be selected by default
    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThan(0)
    expect(options[0]).toHaveAttribute('aria-selected', 'true')

    // Arrow down moves to second if there are multiple
    if (options.length > 1) {
      await user.keyboard('{ArrowDown}')
      expect(options[1]).toHaveAttribute('aria-selected', 'true')
    }
  })

  it('selects with Enter key', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<Search onSelect={onSelect} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flinders')
    await user.keyboard('{Enter}')

    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('closes results on Escape', async () => {
    const user = userEvent.setup()
    render(<Search onSelect={() => {}} />)

    const input = screen.getByRole('combobox')
    await user.type(input, 'Flinders')
    expect(screen.getByRole('listbox')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})

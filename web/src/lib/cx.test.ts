import { describe, it, expect } from 'vitest'
import { cx } from './cx'

describe('cx', () => {
  it('joins class names with spaces', () => {
    expect(cx('a', 'b', 'c')).toBe('a b c')
  })

  it('filters out falsy values', () => {
    expect(cx('a', false, null, undefined, 'b')).toBe('a b')
  })

  it('returns empty string for no truthy values', () => {
    expect(cx(false, null, undefined)).toBe('')
  })

  it('returns empty string for no arguments', () => {
    expect(cx()).toBe('')
  })

  it('handles a single class name', () => {
    expect(cx('only')).toBe('only')
  })
})

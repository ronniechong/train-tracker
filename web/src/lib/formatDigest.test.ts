import { describe, it, expect } from 'vitest'
import { formatPercent, parseNarrative } from './formatDigest'

describe('formatPercent', () => {
  it('keeps a decimal when the source value has one', () => {
    expect(formatPercent(94.7)).toBe('94.7%')
  })

  it('does not add a decimal for a whole number', () => {
    expect(formatPercent(93)).toBe('93%')
  })
})

describe('parseNarrative', () => {
  it('marks **bold** spans and leaves the rest unbold', () => {
    expect(parseNarrative('Metro delivered **94.7%** on time.')).toEqual([
      { text: 'Metro delivered ', bold: false },
      { text: '94.7%', bold: true },
      { text: ' on time.', bold: false },
    ])
  })

  it('strips a leading Markdown heading marker instead of rendering it literally', () => {
    expect(parseNarrative('# Melbourne Metro Performance: 3-9 August 2026 Metro delivered 94.7%.')).toEqual([
      { text: 'Melbourne Metro Performance: 3-9 August 2026 Metro delivered 94.7%.', bold: false },
    ])
  })

  it('strips a heading marker of any level (## etc)', () => {
    expect(parseNarrative('## Weekly summary')).toEqual([{ text: 'Weekly summary', bold: false }])
  })
})

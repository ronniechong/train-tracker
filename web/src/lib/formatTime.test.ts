import { describe, it, expect } from 'vitest'
import { formatDateTime, formatTime } from './formatTime'

// `hour12: true` is explicit in formatTime.ts specifically because several
// real locales (e.g. en-GB) default to 24-hour time with no am/pm marker
// at all -- these assertions lock in that every clock-time render includes
// one regardless of the runtime's locale.
describe('formatTime', () => {
  it('always includes an am/pm marker', () => {
    expect(formatTime('2026-08-18T18:42:00Z')).toMatch(/am|pm/i)
  })

  it('formats as a 12-hour clock time, never 13-23', () => {
    // Timezone-agnostic on purpose -- only asserts the hour component is
    // in 12-hour range, not a specific wall-clock value, since the
    // rendered hour depends on the test runner's local timezone.
    const hour = Number(formatTime('2026-08-18T18:42:00Z').match(/^(\d{1,2}):/)?.[1])
    expect(hour).toBeGreaterThanOrEqual(1)
    expect(hour).toBeLessThanOrEqual(12)
  })
})

describe('formatDateTime', () => {
  it('always includes an am/pm marker', () => {
    expect(formatDateTime('2026-08-18T18:42:00Z')).toMatch(/am|pm/i)
  })
})

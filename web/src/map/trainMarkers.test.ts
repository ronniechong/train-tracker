import { describe, expect, it } from 'vitest'
import { nextStopLabel } from './trainMarkers'
import type { Train } from '../api-types'

function makeTrain(overrides: Partial<Train> = {}): Train {
  return {
    trip_id: 't1',
    route_id: 'r1',
    status: 'live',
    latitude: 0,
    longitude: 0,
    bearing: 0,
    position_updated_at: null,
    schedule_updated_at: null,
    last_seen_at: null,
    start_time: null,
    trip_headsign: null,
    direction_id: null,
    next_stop_id: null,
    next_stop_name: null,
    next_stop_delay_seconds: null,
    ...overrides,
  }
}

describe('nextStopLabel', () => {
  it('is null when there is no next stop name', () => {
    expect(nextStopLabel(makeTrain())).toBeNull()
  })

  it('omits the delay suffix within the on-time band', () => {
    expect(nextStopLabel(makeTrain({ next_stop_name: 'Richmond', next_stop_delay_seconds: 30 }))).toBe(
      'Next: Richmond',
    )
  })

  it('reports lateness above the on-time band', () => {
    expect(
      nextStopLabel(makeTrain({ next_stop_name: 'Richmond', next_stop_delay_seconds: 180 })),
    ).toBe('Next: Richmond, 3 min late')
  })

  it('reports earliness for a negative delay', () => {
    expect(
      nextStopLabel(makeTrain({ next_stop_name: 'Richmond', next_stop_delay_seconds: -120 })),
    ).toBe('Next: Richmond, 2 min early')
  })

  it('omits the suffix entirely when delay is unknown', () => {
    expect(nextStopLabel(makeTrain({ next_stop_name: 'Richmond', next_stop_delay_seconds: null }))).toBe(
      'Next: Richmond',
    )
  })
})

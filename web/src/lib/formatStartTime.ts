// GTFS-RT `start_time` is "HH:MM:SS", never a JS-parseable time -- hours can
// exceed 24 for a post-midnight service (GTFS spec convention, still "the
// same service day"). Formats as "5pm" when on the hour, "5:04pm" otherwise.
export function formatStartTime(value: string | null): string | null {
  if (!value) return null
  const match = /^(\d{1,2}):(\d{2}):\d{2}$/.exec(value)
  if (!match) return null
  const rawHour = Number(match[1])
  const minute = Number(match[2])
  const hour24 = rawHour % 24
  const period = hour24 < 12 ? 'am' : 'pm'
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12
  return minute === 0 ? `${hour12}${period}` : `${hour12}:${String(minute).padStart(2, '0')}${period}`
}

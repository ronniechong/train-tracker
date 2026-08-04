// Absolute clock-time formatting (e.g. "6:42 PM"), as opposed to
// relativeTime.ts's elapsed-duration formatting ("6s ago"). Uses the
// browser's own locale via Intl rather than a hardcoded format or a new
// i18n dependency -- native, zero-cost, correct for whoever's viewing.
const formatter = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

export function formatTime(iso: string): string {
  return formatter.format(new Date(iso))
}

// Date + time (e.g. "4 Aug, 6:42 PM"), for values that can plausibly span
// day boundaries -- a bare time is ambiguous once a value could be
// yesterday (e.g. an alert's active_period.start) rather than always
// "today". Same locale-native approach as formatTime above.
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
})

export function formatDateTime(iso: string): string {
  return dateTimeFormatter.format(new Date(iso))
}

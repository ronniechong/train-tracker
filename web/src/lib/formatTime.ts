// Absolute clock-time formatting (e.g. "6:42 PM"), as opposed to
// relativeTime.ts's elapsed-duration formatting ("6s ago"). Uses the
// browser's own locale via Intl rather than a hardcoded format or a new
// i18n dependency -- native, zero-cost, correct for whoever's viewing.
const formatter = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

export function formatTime(iso: string): string {
  return formatter.format(new Date(iso))
}

// Shared by WeeklyDigestPanel: date-range and percentage formatting for
// weekly digest content, split out from formatTime.ts since these format
// plain dates/numbers rather than instants -- same Intl-singleton pattern
// (native, zero-cost, correct for whoever's viewing) as formatTime.ts.
const dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const percentFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

// week_start/week_end are plain "YYYY-MM-DD" dates (no time component) --
// parsing as UTC and formatting with a UTC-anchored Intl call avoids the
// off-by-one-day bug a local-timezone Date() parse of a date-only string
// can produce for viewers west of UTC.
export function formatWeekRange(weekStart: string, weekEnd: string): string {
  const start = new Date(`${weekStart}T00:00:00Z`)
  const end = new Date(`${weekEnd}T00:00:00Z`)
  return `${dateFormatter.format(start)} – ${dateFormatter.format(end)}`
}

// `pct` is already 0-100 (see api-types.ts's WeeklyDigest docstring) --
// this only handles locale-aware rounding/formatting, not the /100 a
// `style: 'percent'` Intl formatter would otherwise apply.
export function formatPercent(pct: number): string {
  return `${percentFormatter.format(pct)}%`
}

// The digest narrative is Haiku's free-text output (ai/weekly_digest.py) --
// its system prompt doesn't forbid Markdown, and it reliably reaches for
// **bold** for emphasis. Rather than round-trip through a Markdown library
// for one syntax element, split on `**...**` pairs and mark the bold spans
// so the caller can render them as real <strong> elements -- no HTML
// parsing/injection risk, since this never touches innerHTML.
export interface NarrativeSpan {
  text: string
  bold: boolean
}

export function parseNarrative(narrative: string): NarrativeSpan[] {
  const parts = narrative.split(/\*\*(.+?)\*\*/g)
  // String.split with a capturing group alternates [unmatched, captured,
  // unmatched, captured, ...] -- odd indices are always the bolded text.
  // Compute `bold` from each part's original index BEFORE filtering out
  // empty strings (e.g. narrative starting with "**") -- filtering first
  // would shift indices and silently flip which spans render bold.
  return parts.map((part, index) => ({ text: part, bold: index % 2 === 1 })).filter((span) => span.text.length > 0)
}

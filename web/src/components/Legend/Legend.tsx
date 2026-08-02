import { geometry, type Route } from '../../geometry'
import { Section } from '../Section'
import { Toggle } from '../Toggle'
import styles from './Legend.module.css'

// Groups lines by color family to match PTV's own published line-color
// spec sheet (Ronnie's reference, 2026-07-30), City Circle pinned first
// per his ask -- not alphabetical, which scattered same-colored lines
// apart from each other. Stony Point isn't in that spec sheet but shares
// Frankston's color/family, so it's placed right after Williamstown.
// Anything not listed here (future new routes) falls back to alphabetical
// at the end rather than silently vanishing.
const LEGEND_ORDER = [
  'City Circle',
  'Sandringham',
  'Frankston',
  'Werribee',
  'Williamstown',
  'Stony Point',
  'Cranbourne',
  'Pakenham',
  'Belgrave',
  'Lilydale',
  'Alamein',
  'Glen Waverley',
  'Sunbury',
  'Craigieburn',
  'Upfield',
  'Mernda',
  'Hurstbridge',
  'Flemington Racecourse',
]

function sortedForLegend(routes: Route[]): Route[] {
  return [...routes].sort((a, b) => {
    const indexA = LEGEND_ORDER.indexOf(a.name)
    const indexB = LEGEND_ORDER.indexOf(b.name)
    if (indexA === -1 && indexB === -1) return a.name.localeCompare(b.name)
    if (indexA === -1) return 1
    if (indexB === -1) return -1
    return indexA - indexB
  })
}

interface LegendProps {
  hiddenRouteIds: ReadonlySet<string>
  /** Fires immediately on toggle so `MapView` can re-sync train markers
   * without waiting for the next SSE delta (up to ~10s later). */
  onToggle: (routeId: string, visible: boolean) => void
}

export function Legend({ hiddenRouteIds, onToggle }: LegendProps) {
  return (
    <Section title="Lines" className={styles.legendSection}>
      <ul className={styles.list}>
        {sortedForLegend(geometry.routes).map((route) => {
          const visible = !hiddenRouteIds.has(route.id)
          return (
            <li key={route.id}>
              <label className={styles.itemRow}>
                <span className={styles.nameGroup}>
                  <span className={styles.swatch} style={{ backgroundColor: route.color }} />
                  <span>{route.name}</span>
                </span>
                <Toggle
                  checked={visible}
                  onChange={(checked) => onToggle(route.id, checked)}
                  aria-label={`Show ${route.name} line`}
                />
              </label>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

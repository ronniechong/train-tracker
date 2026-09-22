import { LINE_DASH_ARRAY, lineStyleForRouteName, vlineGeometry } from '../../vlineGeometry'
import { Section } from '../Section'
import { Toggle } from '../Toggle'
import { trackEvent } from '../../lib/analytics'
import styles from './VlineLegend.module.css'

const VLINE_BRAND_COLOR = '#7F0D82'

/** One corridor per distinct route NAME (a corridor generally has 2-3
 * route_ids for branch/direction variants -- toggling any of them off
 * hides the whole corridor, which is the resolution a user actually
 * thinks in terms of, not individual route_ids). */
function corridors(): { name: string; routeIds: string[] }[] {
  const byName = new Map<string, string[]>()
  for (const route of vlineGeometry.routes) {
    if (!byName.has(route.name)) byName.set(route.name, [])
    byName.get(route.name)!.push(route.id)
  }
  return [...byName.entries()]
    .map(([name, routeIds]) => ({ name, routeIds }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** Renders a short line-style preview (solid/dashed/dotted/etc.) instead
 * of a colour swatch -- V/Line's brand has one colour for the whole mode,
 * so corridor identity is carried by line style, not colour. */
function LineStylePreview({ routeName }: { routeName: string }) {
  const dashArray = LINE_DASH_ARRAY[lineStyleForRouteName(routeName)]
  return (
    <svg className={styles.preview} viewBox="0 0 24 10" width="24" height="10" aria-hidden="true">
      <line
        x1="1" y1="5" x2="23" y2="5"
        stroke={VLINE_BRAND_COLOR}
        strokeWidth="2.5"
        strokeDasharray={dashArray?.join(' ')}
      />
    </svg>
  )
}

interface VlineLegendProps {
  hiddenRouteIds: ReadonlySet<string>
  onToggle: (routeId: string, visible: boolean) => void
}

export function VlineLegend({ hiddenRouteIds, onToggle }: VlineLegendProps) {
  return (
    <Section title="Lines" className={styles.legendSection}>
      <ul className={styles.list}>
        {corridors().map(({ name, routeIds }) => {
          const visible = routeIds.some((id) => !hiddenRouteIds.has(id))
          return (
            <li key={name}>
              <label className={styles.itemRow}>
                <span className={styles.nameGroup}>
                  <LineStylePreview routeName={name} />
                  <span>{name}</span>
                </span>
                <Toggle
                  checked={visible}
                  onChange={(checked) => {
                    trackEvent('toggle-route-visibility', name)
                    for (const routeId of routeIds) onToggle(routeId, checked)
                  }}
                  aria-label={`Show ${name} line`}
                />
              </label>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

import { Section, Placeholder } from './Section'
import styles from './StationPanel.module.css'

// Stub -- real logic (client-side geofence match against live trains on
// station click) is M4 Stage 4 remainder, sequenced after 3b. `grow`
// mirrors the vanilla layout's `#selected-station { flex: 1 0 auto }` so
// this section pushes CTA/footer to the bottom of the sidebar column.
export function StationPanel() {
  return (
    <Section title="Station" className={styles.grow}>
      <Placeholder>Click a station on the map — Stage 4</Placeholder>
    </Section>
  )
}

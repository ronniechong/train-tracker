import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Toggle.module.css'

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
  'aria-label'?: string
  /** Rendered inside the sliding thumb -- caller decides what to show for
   * the current `checked` state (e.g. a sun/moon swap), Toggle itself
   * stays state-agnostic. Bumps the track/thumb to a bigger size, since
   * the default dimensions (Legend's/StatusPanel's plain switches) are
   * too small to hold a glyph. */
  icon?: ReactNode
}

/** Shared switch UI -- extracted from Legend's per-route toggle so
 * StatusPanel's "hide ghost trains" control isn't a second copy of the
 * same markup/CSS. */
export function Toggle({ checked, onChange, 'aria-label': ariaLabel, icon }: ToggleProps) {
  return (
    <span className={cx(styles.toggle, icon != null && styles.withIcon)}>
      <input
        type="checkbox"
        checked={checked}
        aria-label={ariaLabel}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={styles.track}>
        <span className={styles.thumb}>{icon}</span>
      </span>
    </span>
  )
}

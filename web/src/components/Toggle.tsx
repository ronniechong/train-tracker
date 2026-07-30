import styles from './Toggle.module.css'

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
  'aria-label'?: string
}

/** Shared switch UI -- extracted from Legend's per-route toggle so
 * StatusPanel's "hide ghost trains" control isn't a second copy of the
 * same markup/CSS. */
export function Toggle({ checked, onChange, 'aria-label': ariaLabel }: ToggleProps) {
  return (
    <span className={styles.toggle}>
      <input
        type="checkbox"
        checked={checked}
        aria-label={ariaLabel}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={styles.track}>
        <span className={styles.thumb} />
      </span>
    </span>
  )
}

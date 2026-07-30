import { cx } from '../lib/cx'
import styles from './LoadingOverlay.module.css'

interface LoadingOverlayProps {
  visible: boolean
}

export function LoadingOverlay({ visible }: LoadingOverlayProps) {
  return (
    <div className={cx(styles.overlay, !visible && styles.hidden)}>
      <svg className={styles.spinner} viewBox="0 0 50 50" width="40" height="40" aria-hidden="true">
        <circle className={styles.spinnerTrack} cx="25" cy="25" r="20" fill="none" strokeWidth="4" />
        <circle className={styles.spinnerArc} cx="25" cy="25" r="20" fill="none" strokeWidth="4" />
      </svg>
      <p>Loading live map…</p>
    </div>
  )
}

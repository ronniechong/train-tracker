import styles from './Header.module.css'

export function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.titleRow}>
        <img src="/favicon.svg" alt="" width={28} height={28} />
        <h1 className={styles.title}>Melbourne Train Tracker</h1>
      </div>
      <p className={styles.tagline}>Melbourne Metro, close to real-time 😅</p>
    </header>
  )
}

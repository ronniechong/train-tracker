import type { ElementType, HTMLAttributes, ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Section.module.css'

interface SectionProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType
  title?: string
  children: ReactNode
}

/** Shared bordered-card chrome used by every sidebar block (legend,
 * search, status, station panel, CTA, footer) — the one visual pattern
 * the vanilla version applied via a generic `#sidebar section, #sidebar
 * footer` selector. `as` lets the footer render a real `<footer>` while
 * sharing the same styling. */
export function Section({ as: Tag = 'section', title, children, className, ...rest }: SectionProps) {
  return (
    <Tag className={cx(styles.section, className)} {...rest}>
      {title && <p className={styles.title}>{title}</p>}
      {children}
    </Tag>
  )
}

export function Placeholder({ children }: { children: ReactNode }) {
  return <p className={styles.placeholder}>{children}</p>
}

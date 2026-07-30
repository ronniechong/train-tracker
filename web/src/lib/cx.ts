/** Joins CSS Modules class names, dropping falsy values. */
export function cx(...classNames: Array<string | false | undefined | null>): string {
  return classNames.filter(Boolean).join(' ')
}

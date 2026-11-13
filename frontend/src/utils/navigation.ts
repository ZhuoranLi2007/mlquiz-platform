export type QueryValue = string | number | null | undefined | Array<string | number | null>

/** Keep deep-link filters while forcing the destination tab for legacy routes. */
export function compatibilityRedirect(
  path: string,
  currentQuery: Record<string, QueryValue>,
  forcedQuery: Record<string, QueryValue>,
) {
  return {
    path,
    query: { ...currentQuery, ...forcedQuery },
  }
}

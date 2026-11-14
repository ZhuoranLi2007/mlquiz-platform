export function resourceHref(value: unknown): string {
  if (typeof value !== 'string') return ''
  const url = value.trim()
  if (url.startsWith('/') && !url.startsWith('//') && !url.startsWith('/\\')) return url
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : ''
  } catch {
    return ''
  }
}

export function recommendedResourceLinks(primary: any[], fallback: any[] = [], limit = 3): any[] {
  const links: any[] = []
  const seen = new Set<string>()
  for (const resource of [...primary, ...fallback]) {
    const url = resourceHref(resource?.url)
    if (!url || seen.has(url)) continue
    seen.add(url)
    links.push({ ...resource, url })
    if (links.length >= limit) break
  }
  return links
}

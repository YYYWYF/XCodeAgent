export type ServiceLogSegment = { text: string; start: number; matched: boolean }

/** 按不区分大小写的字面量拆分日志，保留换行、原文大小写及安全的 React 文本渲染。 */
export function splitServiceLogMatches(content: string, query: string): ServiceLogSegment[] {
  if (!query) return [{ text: content, start: 0, matched: false }]
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
  const segments: ServiceLogSegment[] = []
  let cursor = 0
  for (const match of content.matchAll(pattern)) {
    const start = match.index!
    if (start > cursor)
      segments.push({ text: content.slice(cursor, start), start: cursor, matched: false })
    segments.push({ text: match[0], start, matched: true })
    cursor = start + match[0].length
  }
  if (cursor < content.length)
    segments.push({ text: content.slice(cursor), start: cursor, matched: false })
  return segments
}

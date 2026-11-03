import type { BackendLogEntry } from '../../service/backendLogs'

/** 将采集时间转换为本地可读时间，异常输入保留原文便于诊断。 */
export function logTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('sv-SE', { hour12: false })
}

/** 按逻辑条目筛选和排序，保持模型正文及异常堆栈内部顺序。 */
export function visibleLogEntries(
  entries: BackendLogEntry[],
  query: string,
  latestFirst: boolean
): BackendLogEntry[] {
  const keyword = query.trim().toLocaleLowerCase()
  const visible = entries.filter(
    (entry) => !keyword || formatLogEntry(entry).toLocaleLowerCase().includes(keyword)
  )
  return latestFirst ? visible.reverse() : visible
}

/** 统一界面和复制文本的时间、级别、来源与正文格式。 */
export function formatLogEntry(entry: BackendLogEntry): string {
  return `${logTime(entry.timestamp)} | ${entry.level} | ${entry.source} | ${entry.text.replace(/\n$/, '')}`
}

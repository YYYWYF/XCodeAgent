import { Empty, Spin } from 'antd'
import { useLayoutEffect, useRef } from 'react'
import type { ReactElement } from 'react'
import type { BackendLogEntry } from '../../service/backendLogs'
import { splitServiceLogMatches } from '../BrowserPreviewPanel/serviceLogSearch'
import { formatLogEntry } from './backendLogPresentation'

type Props = {
  entries: BackendLogEntry[]
  query: string
  latestFirst: boolean
  loading: boolean
}

/** 展示可选择的日志，在最新位置跟随更新并保持历史阅读锚点。 */
export default function BackendLogContent({
  entries,
  query,
  latestFirst,
  loading
}: Props): ReactElement {
  const viewport = useRef<HTMLDivElement>(null)
  const anchor = useRef<{ id: string; offset: number }>()
  const following = useRef(true)
  const previousOrder = useRef(latestFirst)

  /** 保存首个可见日志条目，下一次更新按稳定身份补偿位置。 */
  const rememberPosition = (): void => {
    const node = viewport.current
    if (!node) return
    following.current = latestFirst
      ? node.scrollTop < 24
      : node.scrollHeight - node.scrollTop - node.clientHeight < 24
    const top = node.getBoundingClientRect().top
    const row = Array.from(node.querySelectorAll<HTMLElement>('[data-log-id]')).find(
      (item) => item.getBoundingClientRect().bottom > top
    )
    anchor.current = row?.dataset.logId
      ? { id: row.dataset.logId, offset: row.getBoundingClientRect().top - top }
      : undefined
  }

  useLayoutEffect(() => {
    const node = viewport.current
    if (!node) return
    const reordered = previousOrder.current !== latestFirst
    previousOrder.current = latestFirst
    if ((following.current && !query) || reordered) {
      node.scrollTop = latestFirst ? 0 : node.scrollHeight
    } else if (anchor.current) {
      const saved = anchor.current
      const row = Array.from(node.querySelectorAll<HTMLElement>('[data-log-id]')).find(
        (item) => item.dataset.logId === saved.id
      )
      if (row)
        node.scrollTop +=
          row.getBoundingClientRect().top - node.getBoundingClientRect().top - saved.offset
    }
    rememberPosition()
    // 位置读取只在日志、搜索或排序变化时执行。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, query, latestFirst])

  return (
    <div
      ref={viewport}
      className="backend-log-viewport"
      aria-label="Python 后端日志"
      onScroll={rememberPosition}
    >
      {entries.length ? (
        entries.map((entry) => (
          <pre key={entry.id} data-log-id={entry.id} className="backend-log-entry">
            {splitServiceLogMatches(formatLogEntry(entry), query.trim()).map((segment) =>
              segment.matched ? <mark key={segment.start}>{segment.text}</mark> : segment.text
            )}
          </pre>
        ))
      ) : loading ? (
        <div className="backend-log-empty">
          <Spin tip="正在读取后端日志…" />
        </div>
      ) : (
        <div className="backend-log-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={query ? '没有匹配的日志' : '暂无后端日志'}
          />
        </div>
      )}
    </div>
  )
}

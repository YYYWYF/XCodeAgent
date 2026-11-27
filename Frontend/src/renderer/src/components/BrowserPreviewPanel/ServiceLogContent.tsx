import { CopyOutlined } from '@ant-design/icons'
import { Dropdown, Empty, Input } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { splitServiceLogMatches } from './serviceLogSearch'
import './ServiceLogContent.less'

type Props = {
  content: string
  label: string
  emptyMessage?: string
  searchLabel?: string
  following: boolean
  expanded: boolean
  viewportHeight?: number
  onPauseFollowing: () => void
  onCopy: (text: string) => Promise<void>
}

/** 展示可选取的日志，并在右键菜单中复制选区或当前内容。 */
export default function ServiceLogContent({
  content,
  label,
  emptyMessage = '本次启动暂无日志',
  searchLabel = '搜索启动日志',
  following,
  expanded,
  viewportHeight,
  onPauseFollowing,
  onCopy
}: Props): ReactElement {
  const logRef = useRef<HTMLPreElement>(null)
  const [selectedText, setSelectedText] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState({ query: '' })
  const segments = useMemo(
    () => splitServiceLogMatches(content, search.query),
    [content, search.query]
  )
  const matchCount = segments.filter((segment) => segment.matched).length

  useEffect(() => {
    if (following && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [content, following, label, expanded, viewportHeight])

  useEffect(() => {
    const log = logRef.current
    const match = log?.querySelector('mark')
    if (!log || !match || !search.query) return
    // 只滚动日志自身，避免定位搜索结果时带动整个抽屉。
    log.scrollTop += match.getBoundingClientRect().top - log.getBoundingClientRect().top - 13
  }, [search])

  /** 显式提交后才应用高亮，并暂停跟随，避免新日志抢走正在查看的结果。 */
  const searchLogs = (value: string): void => {
    const query = value.trim()
    if (query) onPauseFollowing()
    setSearch({ query })
  }

  /** 菜单获取焦点前保存当前日志内的选区，排除其他页面区域选中的文本。 */
  const captureSelection = (): void => {
    const selection = window.getSelection()
    const log = logRef.current
    setSelectedText(
      log && selection && log.contains(selection.anchorNode) && log.contains(selection.focusNode)
        ? selection.toString()
        : ''
    )
  }

  return (
    <div className="preview-log-content">
      <div className="preview-log-search">
        <span className="preview-log-search__count" role="status" aria-live="polite">
          {search.query ? (matchCount ? `${matchCount} 处匹配` : '无匹配结果') : ''}
        </span>
        <Input.Search
          aria-label={searchLabel}
          placeholder="搜索日志"
          size="small"
          allowClear
          value={searchInput}
          disabled={!content}
          onChange={(event) => {
            setSearchInput(event.target.value)
            if (!event.target.value) setSearch({ query: '' })
          }}
          onSearch={searchLogs}
        />
      </div>
      {!content ? (
        <div className="preview-log-empty">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyMessage} />
        </div>
      ) : (
        <Dropdown
          trigger={['contextMenu']}
          overlayClassName="preview-log-context-menu"
          menu={{
            items: [
              {
                key: 'selection',
                label: '复制所选内容',
                icon: <CopyOutlined />,
                disabled: !selectedText
              },
              { key: 'all', label: '复制全部日志', icon: <CopyOutlined /> }
            ],
            onClick: ({ key }) => void onCopy(key === 'selection' ? selectedText : content)
          }}
        >
          <pre
            ref={logRef}
            className="preview-service-logs"
            aria-label={label}
            onContextMenu={captureSelection}
            onScroll={(event) => {
              const node = event.currentTarget
              if (node.scrollHeight - node.scrollTop - node.clientHeight > 24) onPauseFollowing()
            }}
          >
            {segments.map((segment) =>
              segment.matched ? (
                <mark key={segment.start} className="preview-log-match">
                  {segment.text}
                </mark>
              ) : (
                segment.text
              )
            )}
          </pre>
        </Dropdown>
      )}
    </div>
  )
}

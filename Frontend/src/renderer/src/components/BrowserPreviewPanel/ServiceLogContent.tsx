import { CopyOutlined } from '@ant-design/icons'
import { Dropdown, Empty } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import './ServiceLogContent.less'

type Props = {
  content: string
  label: string
  following: boolean
  expanded: boolean
  onPauseFollowing: () => void
  onCopy: (text: string) => Promise<void>
}

/** 展示可选择的启动日志，并在右键菜单中复制选区或当前端的全部日志。 */
export default function ServiceLogContent({
  content,
  label,
  following,
  expanded,
  onPauseFollowing,
  onCopy
}: Props): ReactElement {
  const logRef = useRef<HTMLPreElement>(null)
  const [selectedText, setSelectedText] = useState('')

  useEffect(() => {
    if (following && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [content, following, label, expanded])

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

  if (!content) {
    return (
      <div className="preview-log-empty">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次启动暂无日志" />
      </div>
    )
  }

  return (
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
        {content}
      </pre>
    </Dropdown>
  )
}

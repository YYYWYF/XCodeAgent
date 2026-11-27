import {
  CheckOutlined,
  CopyOutlined,
  DownOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined
} from '@ant-design/icons'
import { Alert, Button } from 'antd'
import { useId, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactElement } from 'react'
import { previewServiceAttemptId } from '../../../../service/previewRuntime'
import type { PreviewRuntimePayload, PreviewServiceLayer } from '../../../../service/previewRuntime'
import ServiceLogContent from '../../../BrowserPreviewPanel/ServiceLogContent'
import { useServiceLogClipboard } from '../../../BrowserPreviewPanel/useServiceLogClipboard'
import './DebugRuntime.less'

type Props = {
  id: string
  layer: PreviewServiceLayer
  runtime?: PreviewRuntimePayload['runtime']
  logsExpanded: boolean
  onExpandedChange: (expanded: boolean) => void
  onClose: () => void
}

/** 复用接口调试的日志布局，按服务分端展示并支持从顶部调节高度。 */
export default function DebugServiceLogs({
  id,
  layer,
  runtime,
  logsExpanded,
  onExpandedChange,
  onClose
}: Props): ReactElement {
  const headingId = useId()
  const [following, setFollowing] = useState(true)
  const [height, setHeight] = useState<number>()
  const panelRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ pointerId: number; y: number; height: number }>()
  const { copied, copyError, copyLogs } = useServiceLogClipboard()
  const label = layer === 'backend' ? '后端' : '前端'
  const logs = runtime?.logs?.[layer] ?? []
  const content = logs
    .map(
      (entry) =>
        `[${entry.stage} · ${entry.stream}] ${entry.name}${entry.truncated ? '（日志已截断，仅显示末尾）' : ''}\n${entry.content}`
    )
    .join('\n\n')

  /** 限制拖动高度，给上方调试区留出最小空间。 */
  function resize(next: number): void {
    const available = panelRef.current?.parentElement?.clientHeight ?? 0
    if (!available) return
    const max = Math.max(0, available - 96)
    setHeight(Math.min(max, Math.max(176, next)))
    if (logsExpanded) onExpandedChange(false)
  }

  /** 使用指针捕获，使指针移出拖动条时仍能调整面板。 */
  function startDrag(event: PointerEvent<HTMLButtonElement>): void {
    if (!event.isPrimary || event.button !== 0 || !panelRef.current) return
    dragRef.current = {
      pointerId: event.pointerId,
      y: event.clientY,
      height: panelRef.current.offsetHeight
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  /** 面板固定在底边，拖动顶部改变它占用的高度。 */
  function moveDrag(event: PointerEvent<HTMLButtonElement>): void {
    const drag = dragRef.current
    if (drag?.pointerId === event.pointerId) resize(drag.height + drag.y - event.clientY)
  }

  /** 结束本次拖动，避免窗口失焦后沿用旧起点。 */
  function endDrag(): void {
    dragRef.current = undefined
  }

  /** 键盘用户也能用上下方向键调节日志高度。 */
  function resizeWithKeyboard(event: KeyboardEvent<HTMLButtonElement>): void {
    if (!panelRef.current || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
    event.preventDefault()
    resize(panelRef.current.offsetHeight + (event.key === 'ArrowUp' ? 24 : -24))
  }

  return (
    <div
      id={id}
      ref={panelRef}
      className="debug-service-logs"
      data-expanded={logsExpanded}
      style={
        height === undefined
          ? undefined
          : ({ '--debug-log-height': `${height}px` } as CSSProperties)
      }
    >
      <button
        type="button"
        className="debug-service-logs__grip"
        aria-label="调整运行日志高度，可使用上下方向键"
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onLostPointerCapture={endDrag}
        onKeyDown={resizeWithKeyboard}
      >
        <span aria-hidden="true" />
      </button>
      <section className="preview-log-panel" aria-labelledby={headingId}>
        <div className="preview-log-panel__header">
          <div className="preview-section-heading">
            <div>
              <h3 id={headingId}>运行日志</h3>
            </div>
          </div>
          <div className="preview-log-panel__summary">
            <span className="preview-log-count">
              {logs.length} 个{label}日志文件
            </span>
            <Button
              type="text"
              size="small"
              aria-label="收起日志"
              icon={<DownOutlined />}
              onClick={onClose}
            />
          </div>
        </div>
        <div className="preview-log-toolbar">
          <span className="preview-log-toolbar__hint">
            <span className="preview-log-live-dot" />
            {following ? '正在跟随最新输出' : '已暂停自动滚动'}
          </span>
          <div className="preview-log-toolbar__actions">
            <Button
              size="small"
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              disabled={!content}
              onClick={() => void copyLogs(content)}
            >
              {copied ? '已复制' : '复制日志'}
            </Button>
            <Button
              size="small"
              className={following ? 'is-following' : ''}
              onClick={() => setFollowing(!following)}
            >
              {following ? '跟随最新' : '恢复跟随'}
            </Button>
            <Button
              size="small"
              icon={logsExpanded ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
              aria-label={logsExpanded ? '还原日志高度' : '展开运行日志'}
              aria-pressed={logsExpanded}
              onClick={() => onExpandedChange(!logsExpanded)}
            >
              {logsExpanded ? '还原' : '展开'}
            </Button>
          </div>
        </div>
        {copyError && (
          <Alert
            type="error"
            showIcon
            message={copyError}
            className="preview-service-callout is-error"
          />
        )}
        <ServiceLogContent
          key={previewServiceAttemptId(runtime, layer)}
          content={content}
          label={`${label}运行日志`}
          searchLabel={`搜索${label}运行日志`}
          emptyMessage={`暂无${label}日志`}
          following={following}
          expanded={logsExpanded}
          viewportHeight={height}
          onPauseFollowing={() => setFollowing(false)}
          onCopy={copyLogs}
        />
      </section>
    </div>
  )
}

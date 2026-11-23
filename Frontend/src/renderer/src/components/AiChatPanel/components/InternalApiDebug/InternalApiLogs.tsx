import { CheckOutlined, CopyOutlined, DownOutlined, FullscreenExitOutlined, FullscreenOutlined } from '@ant-design/icons'
import { Alert, Button } from 'antd'
import { useId, useState } from 'react'
import type { ReactElement } from 'react'
import type { PreviewRuntimePayload } from '../../../../service/previewRuntime'
import ServiceLogContent from '../../../BrowserPreviewPanel/ServiceLogContent'
import { useServiceLogClipboard } from '../../../BrowserPreviewPanel/useServiceLogClipboard'

type Props = {
  runtime?: PreviewRuntimePayload['runtime']
  logsExpanded: boolean
  onExpandedChange: (expanded: boolean) => void
  onClose: () => void
}

/** 展示后端构建、启动和运行日志，复用预览的日志交互。 */
export default function InternalApiLogs({ runtime, logsExpanded, onExpandedChange, onClose }: Props): ReactElement {
  const headingId = useId()
  const [following, setFollowing] = useState(true)
  const { copied, copyError, copyLogs } = useServiceLogClipboard()
  const logs = runtime?.logs?.backend ?? []
  const content = logs.map(
    (entry) =>
      `[${entry.stage} · ${entry.stream}] ${entry.name}${entry.truncated ? '（日志已截断，仅显示末尾）' : ''}\n${entry.content}`
  ).join('\n\n')

  return (
    <section className="preview-log-panel" aria-labelledby={headingId}>
      <div className="preview-log-panel__header">
        <div className="preview-section-heading">
          <div>
            <h3 id={headingId}>运行日志</h3>
          </div>
        </div>
        <div className="preview-log-panel__summary">
          <span className="preview-log-count">
            {logs.length} 个后端日志文件
          </span>
          <Button type="text" size="small" aria-label="收起日志" icon={<DownOutlined />} onClick={onClose} />
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
        key={runtime?.attemptId}
        content={content}
        label="后端运行日志"
        searchLabel="搜索后端运行日志"
        emptyMessage="暂无后端日志"
        following={following}
        expanded={logsExpanded}
        onPauseFollowing={() => setFollowing(false)}
        onCopy={copyLogs}
      />
    </section>
  )
}

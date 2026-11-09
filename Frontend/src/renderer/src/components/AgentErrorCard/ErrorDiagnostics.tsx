import { CheckOutlined, CopyOutlined, DownOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useId, useState, type ReactElement, type ReactNode } from 'react'
import { cx } from '../../utils'
import { useDiagnosticClipboard } from './useDiagnosticClipboard'

/** 共用底部操作与折叠诊断区，重试和详情各占一侧，复制仅在展开区内出现。 */
export default function ErrorDiagnostics({
  text,
  content,
  action
}: {
  text: string
  content?: ReactNode
  action?: ReactNode
}): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const { copied, copying, copyFailed, copyDiagnostic } = useDiagnosticClipboard(text)
  return (
    <div className={cx('agent-error-card-actions')}>
      {action}
      <Button type="text" className={cx('agent-error-card-disclosure')}
        aria-expanded={expanded} aria-controls={detailsId}
        onClick={() => setExpanded(!expanded)}>
        {expanded ? '收起详情' : '错误详情'}
        <DownOutlined rotate={expanded ? 180 : 0} />
      </Button>
      {/* 折叠容器单独占下一行，避免展开后与重试按钮重叠；按钮提供可访问的开合状态。 */}
      <details id={detailsId} className={cx('agent-error-card-details')} open={expanded}>
        <summary hidden>错误详情</summary>
        <div className={cx('agent-error-card-diagnostic-panel')}>
          <div className={cx('agent-error-card-diagnostic-header')}>
            <Typography.Text strong>诊断信息</Typography.Text>
            <Button type="text" className={cx('agent-error-card-copy-button')}
              aria-label={copied ? '已复制' : '复制诊断信息'} loading={copying}
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => { void copyDiagnostic() }}>
              {copied ? '已复制' : '复制诊断'}
            </Button>
          </div>
          {content ?? <pre className={cx('agent-error-card-diagnostic')}>{text}</pre>}
          {copyFailed ? <Typography.Text className={cx('agent-error-card-hint')} role="status">
            复制失败，请重试或选择下方诊断文本手动复制。
          </Typography.Text> : null}
        </div>
      </details>
    </div>
  )
}

import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { Button, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import { useDiagnosticClipboard } from './useDiagnosticClipboard'

/** 展示与原生剪贴板一致的诊断内容，复制失败时保留可手动选择的文本。 */
export default function ErrorDiagnostics({
  text,
  showDetails = true
}: {
  text: string
  showDetails?: boolean
}): ReactElement {
  const { copied, copyFailed, copying, copyDiagnostic } = useDiagnosticClipboard(text)
  const copyLabel = copied ? '已复制' : '复制诊断信息'
  const copyButton = (
    <Tooltip title={copyLabel}>
      <Button
        type="text"
        size="small"
        className={cx('agent-error-card-copy-button')}
        aria-label={copyLabel}
        loading={copying}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        onClick={(event) => {
          // summary 内的复制操作不应切换详情的展开状态。
          event.preventDefault()
          event.stopPropagation()
          void copyDiagnostic()
        }}
      />
    </Tooltip>
  )

  return (
    <>
      {showDetails && (
        <details className={cx('agent-error-card-details')}>
          <summary>错误详情 {copyButton}</summary>
          <pre className={cx('agent-error-card-diagnostic')}>{text}</pre>
        </details>
      )}
      {!showDetails && copyButton}
      {copyFailed && (
        <Typography.Text className={cx('agent-error-card-hint')} role="status">
          {showDetails
            ? '复制失败，请重试或展开错误详情手动复制。'
            : '复制失败，请重试或选择错误文本手动复制。'}
        </Typography.Text>
      )}
    </>
  )
}

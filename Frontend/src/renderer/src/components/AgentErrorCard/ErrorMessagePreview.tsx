import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import { useDiagnosticClipboard } from './useDiagnosticClipboard'
import './AgentErrorCard.less'

const ERROR_MESSAGE_MAX_LINES = 10

/** 将错误摘要限制为十行，复制操作始终使用未截断的完整原文。 */
export default function ErrorMessagePreview({ text }: { text: string }): ReactElement {
  const { copied, copying, copyFailed, copyDiagnostic } = useDiagnosticClipboard(text)
  return (
    <div className={cx('agent-error-card-message-preview')}>
      <div
        className={cx('agent-error-card-message-preview-text')}
        style={{ WebkitLineClamp: ERROR_MESSAGE_MAX_LINES }}
      >
        {text}
      </div>
      <Button
        aria-label={copied ? '已复制完整错误' : '复制完整错误'}
        className={cx('agent-error-card-copy-button', 'agent-error-card-message-preview-copy')}
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        loading={copying}
        onClick={() => {
          void copyDiagnostic()
        }}
        size="small"
        type="text"
      >
        {copied ? '已复制' : '复制完整错误'}
      </Button>
      {copyFailed ? (
        <Typography.Text
          className={cx('agent-error-card-message-preview-copy-status')}
          role="status"
        >
          复制失败，请打开错误详情手动复制。
        </Typography.Text>
      ) : null}
    </div>
  )
}

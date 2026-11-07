import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'

/** 展示与标题复制按钮一致的诊断详情，复制失败时保留可手动选择的文本。 */
export default function ErrorDiagnostics({
  text,
  showDetails = true,
  copyFailed = false
}: {
  text: string
  showDetails?: boolean
  copyFailed?: boolean
}): ReactElement {
  return (
    <>
      {showDetails && (
        <details className={cx('agent-error-card-details')}>
          <summary>错误详情</summary>
          <pre className={cx('agent-error-card-diagnostic')}>{text}</pre>
        </details>
      )}
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

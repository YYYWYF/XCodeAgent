import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

/** 构建检查完成后提供执行或跳过性能测试的双按钮确认面板。 */
export default function FrontendPerformanceConfirmationPanel({
  disabled,
  message,
  onSubmit
}: {
  disabled?: boolean
  message?: string
  onSubmit: (decision: 'skip' | 'run') => void
}): ReactElement {
  return (
    <div className={cx('workflow-frontend-performance-confirmation')}>
      <Typography.Text>
        {message ||
          '构建检查已完成。前端性能测试不是必需步骤，可能耗时较长，是否执行前端性能测试？'}
      </Typography.Text>
      <div className={cx('workflow-frontend-performance-confirmation-actions')}>
        <Button disabled={disabled} onClick={() => onSubmit('run')} type="default">
          执行性能测试
        </Button>
        <Button disabled={disabled} onClick={() => onSubmit('skip')} type="primary">
          跳过性能测试
        </Button>
      </div>
    </div>
  )
}

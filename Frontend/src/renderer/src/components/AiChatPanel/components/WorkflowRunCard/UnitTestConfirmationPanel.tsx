import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

/** 构建检查完成后提供执行或不执行单元测试的双按钮确认面板。 */
export default function UnitTestConfirmationPanel({
  disabled,
  message,
  onSubmit
}: {
  disabled?: boolean
  message?: string
  onSubmit: (decision: 'skip' | 'run') => void
}): ReactElement {
  return (
    <div className={cx('workflow-unit-test-confirmation')}>
      <Typography.Text>
        {message ||
          '构建检查已完成，单元测试不是必须步骤，可以选择跳过。跳过单元测试不会对代码质量产生较大影响。'}
      </Typography.Text>
      <div className={cx('workflow-unit-test-confirmation-actions')}>
        <Button disabled={disabled} onClick={() => onSubmit('run')} type="default">
          执行单元测试
        </Button>
        <Button disabled={disabled} onClick={() => onSubmit('skip')} type="primary">
          不执行单元测试
        </Button>
      </div>
    </div>
  )
}

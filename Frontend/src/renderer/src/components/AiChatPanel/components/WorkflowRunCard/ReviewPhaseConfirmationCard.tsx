import { CheckCircleOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  disabled?: boolean
  diffFileCount?: number
  onSubmit: (mode: 'full' | 'diff') => void
}

/** 渲染集成测试通过后的审查阶段确认卡，确认动作由上层提交结构化协议。 */
export default function ReviewPhaseConfirmationCard({
  disabled,
  diffFileCount = 0,
  onSubmit
}: Props): ReactElement {
  return (
    <div className={cx('workflow-review-phase-confirmation')}>
      <div className={cx('workflow-review-phase-confirmation-title')}>
        <span className={cx('workflow-review-phase-confirmation-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div>
          <Text strong>测试已通过</Text>
          <Text type="secondary">前后端构建与集成质量门禁已通过，请选择审查范围。</Text>
        </div>
      </div>
      <div className={cx('workflow-review-phase-confirmation-actions')}>
        <Button disabled={disabled} onClick={() => onSubmit('full')} type="primary">
          全量审查
        </Button>
        <Button
          disabled={disabled || diffFileCount < 1}
          onClick={() => onSubmit('diff')}
          type="primary"
        >
          Diff 审查
        </Button>
      </div>
      {diffFileCount < 1 ? (
        <Text type="secondary">开发阶段没有可审查的变动文件，暂不能选择 Diff 审查。</Text>
      ) : (
        <Text type="secondary">Diff 审查将读取 {diffFileCount} 个变动文件的完整内容。</Text>
      )}
    </div>
  )
}

import { CheckCircleOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  disabled?: boolean
  onSubmit: () => void
}

/** 渲染集成测试通过后的审查阶段确认卡，确认动作由上层提交结构化协议。 */
export default function ReviewPhaseConfirmationCard({ disabled, onSubmit }: Props): ReactElement {
  return (
    <div className={cx('workflow-review-phase-confirmation')}>
      <div className={cx('workflow-review-phase-confirmation-title')}>
        <span className={cx('workflow-review-phase-confirmation-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div>
          <Text strong>测试已通过</Text>
          <Text type="secondary">前后端构建与集成质量门禁已通过，是否进入审查阶段？</Text>
        </div>
      </div>
      <Button block disabled={disabled} icon={<RightOutlined />} onClick={onSubmit} type="primary">
        进入审查阶段
      </Button>
    </div>
  )
}

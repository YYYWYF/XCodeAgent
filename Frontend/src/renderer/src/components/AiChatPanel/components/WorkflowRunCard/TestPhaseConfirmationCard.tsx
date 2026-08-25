import { CheckCircleOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowTestTarget } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  disabled?: boolean
  target?: WorkflowTestTarget
  onSubmit: () => void
}

const TEST_PHASE_CONFIRMATION_DESCRIPTION =
  '代码生成、Build 与单元测试门禁已完成，确认后将进入测试阶段，执行测试与失败修复'

/** 渲染 Build 完成后的测试阶段进入确认卡。 */
export default function TestPhaseConfirmationCard({
  disabled,
  target,
  onSubmit
}: Props): ReactElement {
  return (
    <div className={cx('workflow-test-phase-confirmation')}>
      <div className={cx('workflow-test-phase-confirmation-title')}>
        <span className={cx('workflow-test-phase-confirmation-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div>
          <Text strong>开发已完成</Text>
          <Text type="secondary">{TEST_PHASE_CONFIRMATION_DESCRIPTION}</Text>
        </div>
      </div>
      {target?.label ? (
        <div className={cx('workflow-test-phase-confirmation-target')}>
          <Text type="secondary">测试目标</Text>
          <Text>{target.label}</Text>
        </div>
      ) : null}
      <Button block disabled={disabled} icon={<RightOutlined />} onClick={onSubmit} type="primary">
        进入测试阶段
      </Button>
    </div>
  )
}

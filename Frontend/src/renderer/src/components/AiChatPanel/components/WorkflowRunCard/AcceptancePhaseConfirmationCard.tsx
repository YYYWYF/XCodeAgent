import { CheckCircleOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MilestoneCommitReminder from '../MilestoneCommitReminder'

const { Text } = Typography

type Props = {
  disabled?: boolean
  onSubmit: () => void
  /** 工作区根目录：驱动卡内的提交提醒。 */
  workspaceRoot?: string
}

/** 渲染代码审查通过后的验收阶段确认卡，提交唯一结构化 confirm 动作。 */
export default function AcceptancePhaseConfirmationCard({
  disabled,
  onSubmit,
  workspaceRoot
}: Props): ReactElement {
  return (
    <div className={cx('workflow-acceptance-phase-confirmation')}>
      <div className={cx('workflow-acceptance-phase-confirmation-title')}>
        <span className={cx('workflow-acceptance-phase-confirmation-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div>
          <Text strong>代码审查已完成</Text>
          <Text type="secondary">审查无问题，确认后将启动项目并进入验收阶段。</Text>
        </div>
      </div>
      {/* 转换前的最后一步：提醒用户先把审查阶段的修复提交掉。 */}
      {workspaceRoot ? (
        <MilestoneCommitReminder
          defaultCommitMessage="fix: 完成审查阶段修复"
          disabled={Boolean(disabled)}
          inline
          milestoneId={`${workspaceRoot}:acceptance-gate`}
          title="审查已完成，建议提交审查修复改动"
          workspaceRoot={workspaceRoot}
        />
      ) : null}
      <Button block disabled={disabled} icon={<RightOutlined />} onClick={onSubmit} type="primary">
        进入验收阶段
      </Button>
    </div>
  )
}

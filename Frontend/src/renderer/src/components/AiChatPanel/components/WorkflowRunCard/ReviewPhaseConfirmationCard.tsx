import { CheckCircleOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MilestoneCommitReminder from '../MilestoneCommitReminder'

const { Text } = Typography

type Props = {
  disabled?: boolean
  /** 开发阶段可审查的变动文件数；为 0 时不允许选 Diff 审查。 */
  diffFileCount?: number
  onSubmit: (mode: 'full' | 'diff') => void
  /** 工作区根目录：驱动卡内的提交提醒。 */
  workspaceRoot?: string
}

/** 渲染集成测试通过后的审查阶段确认卡，确认动作由上层提交结构化协议。 */
export default function ReviewPhaseConfirmationCard({
  disabled,
  diffFileCount = 0,
  onSubmit,
  workspaceRoot
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
      {/* 转换前的最后一步：提醒用户先把测试阶段的修复提交掉。 */}
      {workspaceRoot ? (
        <MilestoneCommitReminder
          defaultCommitMessage="test: 完成测试阶段修复"
          disabled={Boolean(disabled)}
          inline
          milestoneId={`${workspaceRoot}:review-gate`}
          title="测试已通过，建议提交测试与修复改动"
          workspaceRoot={workspaceRoot}
        />
      ) : null}
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

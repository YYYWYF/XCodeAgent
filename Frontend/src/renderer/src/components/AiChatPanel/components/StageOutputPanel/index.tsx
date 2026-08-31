import { NodeIndexOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DagGenerationStageRecord } from '../../../../service/agUiAgent'
import type { WorkflowBuildTaskPlan, WorkflowBuildTaskPlanConfirmation } from '../../../../typings'
import { cx } from '../../../../utils'
import { StageOutput } from '../ProcessSteps/DagGenerationProgress'
import BuildTaskPlanConfirmation from '../WorkflowRunCard/BuildTaskPlanConfirmation'
import './StageOutputPanel.less'

type Props = {
  confirmationDisabled?: boolean
  confirmationErrors?: string[]
  confirmationPlan?: WorkflowBuildTaskPlan
  onConfirmationSubmit?: (action: WorkflowBuildTaskPlanConfirmation) => void
  stage?: DagGenerationStageRecord
}

/** 在右侧“阶段产物”工作区展示当前子阶段，或承载完整 DAG 确认交互卡。 */
export default function StageOutputPanel({
  confirmationDisabled,
  confirmationErrors,
  confirmationPlan,
  onConfirmationSubmit,
  stage
}: Props): ReactElement | null {
  if (confirmationPlan) {
    return (
      <section className={cx('stage-output-panel', 'stage-output-panel-confirmation')}>
        <BuildTaskPlanConfirmation
          disabled={confirmationDisabled || !onConfirmationSubmit}
          dockedActions
          errors={confirmationErrors}
          onSubmit={(action) => onConfirmationSubmit?.(action)}
          plan={confirmationPlan}
        />
      </section>
    )
  }
  if (!stage?.output) return null

  return (
    <section className={cx('stage-output-panel')}>
      <header className={cx('stage-output-panel-header')}>
        <span className={cx('stage-output-panel-icon')} aria-hidden="true">
          <NodeIndexOutlined />
        </span>
        <span className={cx('stage-output-panel-copy')}>
          <Typography.Text strong>{stage.name}</Typography.Text>
          <Typography.Text type="secondary">{stage.detail}</Typography.Text>
        </span>
      </header>
      <div className={cx('stage-output-panel-body')}>
        <StageOutput stage={stage} />
      </div>
    </section>
  )
}

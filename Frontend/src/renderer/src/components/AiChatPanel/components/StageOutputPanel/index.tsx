import { ArrowLeftOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DagGenerationStageRecord } from '../../../../service/agUiAgent'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation
} from '../../../../typings'
import { cx } from '../../../../utils'
import { StageOutput } from '../ProcessSteps/DagGenerationProgress'
import BuildTaskPlanConfirmation from '../WorkflowRunCard/BuildTaskPlanConfirmation'
import './StageOutputPanel.less'

type Props = {
  confirmationDisabled?: boolean
  confirmationErrors?: string[]
  confirmationPlan?: WorkflowBuildTaskPlan
  confirmationTargetReview?: WorkflowBuildTargetReview
  onConfirmationSubmit?: (action: WorkflowBuildTaskPlanConfirmation) => void
  onReturnToConfirmation?: () => void
  stage?: DagGenerationStageRecord
}

/** 在右侧“阶段产物”工作区展示当前子阶段，或承载完整 DAG 确认交互卡。 */
export default function StageOutputPanel({
  confirmationDisabled,
  confirmationErrors,
  confirmationPlan,
  confirmationTargetReview,
  onConfirmationSubmit,
  onReturnToConfirmation,
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
          targetReview={confirmationTargetReview}
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
          {onReturnToConfirmation ? (
            <Typography.Text className={cx('stage-output-panel-return-hint')}>
              当前为历史阶段产物，任务确认仍待处理
            </Typography.Text>
          ) : null}
        </span>
        {onReturnToConfirmation ? (
          <Button
            className={cx('stage-output-panel-return')}
            icon={<ArrowLeftOutlined />}
            onClick={onReturnToConfirmation}
            size="small"
            type="primary"
          >
            返回任务确认
          </Button>
        ) : null}
      </header>
      <div className={cx('stage-output-panel-body')}>
        <StageOutput stage={stage} />
      </div>
    </section>
  )
}

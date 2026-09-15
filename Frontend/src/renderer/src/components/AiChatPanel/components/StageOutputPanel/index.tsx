import { ArrowLeftOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DagGenerationSnapshot } from '../../../../service/agUiAgent'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation
} from '../../../../typings'
import { cx } from '../../../../utils'
import DagGenerationProgress from '../ProcessSteps/DagGenerationProgress'
import BuildTaskPlanConfirmation from '../WorkflowRunCard/BuildTaskPlanConfirmation'
import './StageOutputPanel.less'

type Props = {
  confirmationDisabled?: boolean
  confirmationErrors?: string[]
  confirmationPlan?: WorkflowBuildTaskPlan
  confirmationTargetReview?: WorkflowBuildTargetReview
  onConfirmationSubmit?: (action: WorkflowBuildTaskPlanConfirmation) => void
  onReturnToConfirmation?: () => void
  snapshot?: DagGenerationSnapshot
}

/** 在右侧工作区展示 Unit 进度，或承载完整 DAG 确认交互卡。 */
export default function StageOutputPanel({
  confirmationDisabled,
  confirmationErrors,
  confirmationPlan,
  confirmationTargetReview,
  onConfirmationSubmit,
  onReturnToConfirmation,
  snapshot
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
  if (!snapshot) return null

  return (
    <section className={cx('stage-output-panel')}>
      <header className={cx('stage-output-panel-header')}>
        <span className={cx('stage-output-panel-icon')} aria-hidden="true">
          <NodeIndexOutlined />
        </span>
        <span className={cx('stage-output-panel-copy')}>
          <Typography.Text strong>PlanningRun Unit 进度</Typography.Text>
          <Typography.Text type="secondary">
            展示服务端当前 revision 的完整状态，不推算百分比
          </Typography.Text>
          {onReturnToConfirmation ? (
            <Typography.Text className={cx('stage-output-panel-return-hint')}>
              当前为生成进度，任务确认仍待处理
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
        <DagGenerationProgress snapshot={snapshot} />
      </div>
    </section>
  )
}

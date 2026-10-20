import type { ReactElement } from 'react'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation
} from '../../../../typings'
import { cx } from '../../../../utils'
import BuildTaskPlanConfirmation from '../WorkflowRunCard/BuildTaskPlanConfirmation'
import './StageOutputPanel.less'

type Props = {
  confirmationDisabled?: boolean
  confirmationErrors?: string[]
  confirmationPlan: WorkflowBuildTaskPlan
  confirmationTargetReview?: WorkflowBuildTargetReview
  onConfirmationSubmit?: (action: WorkflowBuildTaskPlanConfirmation) => void
}

/** 在右侧工作区承载完整 DAG 审阅与唯一的确认操作入口。 */
export default function StageOutputPanel({
  confirmationDisabled,
  confirmationErrors,
  confirmationPlan,
  confirmationTargetReview,
  onConfirmationSubmit
}: Props): ReactElement {
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

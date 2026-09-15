import type {
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'
import { workflowClarification } from './components/WorkflowRunCard/workflowClarification'

export type PendingPlanLifecycleRefreshContext = {
  stopped: boolean
  clarificationAnswers?: WorkflowClarificationAnswers
  planControlAction?: 'stop' | 'end' | 'abandon'
}

/** 只为普通 DAG generation-complete transition 调用权威 lifecycle refresh，排除已有 action 收口。 */
export async function maybeRefreshPendingPlanLifecycleAfterGeneration(
  finalWorkflow: WorkflowRunPayload | undefined,
  context: PendingPlanLifecycleRefreshContext,
  refresh: () => Promise<void>
): Promise<boolean> {
  if (
    !finalWorkflow ||
    context.stopped ||
    context.planControlAction ||
    context.clarificationAnswers?.build_task_plan_confirmation ||
    workflowClarification(finalWorkflow)?.mode !== 'build_task_plan_confirmation'
  ) {
    return false
  }
  await refresh()
  return true
}

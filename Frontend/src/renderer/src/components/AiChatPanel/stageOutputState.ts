import type { DagGenerationSnapshot, DagGenerationStageRecord } from '../../service/agUiAgent'
import { processStepsForDisplay } from '../../service/processStepHistory'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowRunPayload
} from '../../typings'
import type { AgentChatMessage } from './types'

export type StageOutputPhase = 'generation' | 'confirmation' | 'other'

/** 从当前会话消息末尾读取最新、最完整的 DAG 生成快照。 */
export function latestDagGenerationSnapshot(
  messages: AgentChatMessage[]
): DagGenerationSnapshot | undefined {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex]
    const steps = processStepsForDisplay(message.processSteps, message.workflow)
    if (!steps?.length) continue
    for (let stepIndex = steps.length - 1; stepIndex >= 0; stepIndex -= 1) {
      const snapshot = steps[stepIndex].dagGeneration
      if (snapshot) return snapshot
    }
  }
  return undefined
}

/** 仅在当前 Workflow 正处于 DAG 确认时读取任务计划，避免历史确认数据污染后续阶段。 */
export function currentDagConfirmationPlan(
  workflow: WorkflowRunPayload | undefined
): WorkflowBuildTaskPlan | undefined {
  const clarification = currentDagConfirmationPayload(workflow)
  if (!clarification) return undefined
  return clarification.taskPlan
}

/** 读取当前 DAG 确认目标，供右侧确认卡展示页面及其实际关联接口。 */
export function currentDagConfirmationTargetReview(
  workflow: WorkflowRunPayload | undefined
): WorkflowBuildTargetReview | undefined {
  return currentDagConfirmationPayload(workflow)?.targetReview
}

/** 读取当前 DAG 确认卡的结构化错误，供右侧交互卡复用原始反馈。 */
export function currentDagConfirmationErrors(workflow: WorkflowRunPayload | undefined): string[] {
  const errors = currentDagConfirmationPayload(workflow)?.errors
  return Array.isArray(errors)
    ? errors.flatMap((error) => (typeof error === 'string' && error.trim() ? [error.trim()] : []))
    : []
}

/** 从 Workflow 当前投影位置解析 DAG 确认载荷，不读取历史阶段快照。 */
function currentDagConfirmationPayload(
  workflow: WorkflowRunPayload | undefined
):
  | {
      taskPlan?: WorkflowBuildTaskPlan
      targetReview?: WorkflowBuildTargetReview
      errors?: unknown
    }
  | undefined {
  if (!workflow) return undefined
  return [
    workflow.summary.clarification,
    workflow.summary.buildTaskPlanConfirmation,
    workflow.state?.clarification,
    workflow.result?.clarification
  ].find(
    (value) =>
      value &&
      typeof value === 'object' &&
      String((value as { mode?: string }).mode || '') === 'build_task_plan_confirmation'
  ) as
    | {
        taskPlan?: WorkflowBuildTaskPlan
        targetReview?: WorkflowBuildTargetReview
        errors?: unknown
      }
    | undefined
}

/** 区分 DAG 生成、DAG 确认和其它大阶段，供右侧面板只在大阶段变化时自动跟随。 */
export function stageOutputPhase(
  workflow: WorkflowRunPayload | undefined,
  snapshot: DagGenerationSnapshot | undefined,
  confirmationPlan: WorkflowBuildTaskPlan | undefined
): StageOutputPhase {
  if (confirmationPlan) return 'confirmation'
  const phase = String(
    workflow?.summary.phase || workflow?.result?.phase || workflow?.state?.phase || ''
  )
  if (phase === 'prepare_build_tasks') return 'generation'
  if (snapshot?.stages.some((stage) => stage.status === 'running')) return 'generation'
  return 'other'
}

/** 返回当前运行中的 DAG 子阶段；没有运行中阶段时不回退到已完成产物。 */
export function runningDagGenerationStage(
  snapshot: DagGenerationSnapshot | undefined
): DagGenerationStageRecord | undefined {
  return snapshot?.stages.find((stage) => stage.status === 'running')
}

/** 按稳定阶段 ID 从当前会话最新快照解析产物，禁止直接持有历史快照对象。 */
export function selectedDagGenerationStage(
  snapshot: DagGenerationSnapshot | undefined,
  stageId: string | undefined
): DagGenerationStageRecord | undefined {
  if (!stageId) return undefined
  return snapshot?.stages.find((stage) => stage.id === stageId)
}

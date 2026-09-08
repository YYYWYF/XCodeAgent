import type { DagGenerationSnapshot, DagGenerationUnitRecord } from '../../service/agUiAgent'
import { newerDagGenerationSnapshot, readDagGenerationSnapshot } from '../../service/agUiAgent'
import { processStepsForDisplay } from '../../service/processStepHistory'
import type {
  ApplicationLifecycle,
  PlanningRefreshState,
  WorkbenchExecution,
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowRunPayload
} from '../../typings'
import type { AgentChatMessage } from './types'

export type StageOutputPhase = 'generation' | 'confirmation' | 'other'

/** 读取并校验 lifecycle GET 临时附加的 Planning refresh 投影。 */
export function planningRefreshState(
  lifecycle: ApplicationLifecycle | undefined
): PlanningRefreshState | undefined {
  const value = lifecycle?.extensions?.planningRefresh
  if (
    !value ||
    value.schemaVersion !== 'planning-refresh.v1' ||
    !['pending', 'abandoned', 'active_planning_run', 'confirmed_plan', 'none'].includes(value.source) ||
    ![
      'awaiting_confirmation',
      'abandoned',
      'planning',
      'planning_run_interrupted',
      'confirmed',
      'idle'
    ].includes(value.status)
  ) {
    return undefined
  }
  return value
}

/** 返回 Backend 重启导致的明确中断状态，禁止把磁盘 active 误当成仍在执行。 */
export function planningRefreshInterruption(
  lifecycle: ApplicationLifecycle | undefined
): PlanningRefreshState | undefined {
  const state = planningRefreshState(lifecycle)
  return state?.status === 'planning_run_interrupted' ? state : undefined
}

/** 从持久化生命周期中读取当前唯一的 Build DAG 待确认 execution。 */
export function pendingDagConfirmationExecution(
  lifecycle: ApplicationLifecycle | undefined
): WorkbenchExecution | undefined {
  const recovery = planningRefreshState(lifecycle)
  if (recovery) {
    if (recovery.source !== 'pending' || recovery.status !== 'awaiting_confirmation') {
      return undefined
    }
    const exact = recovery.workflowRunId
      ? lifecycle?.activeExecutions?.[recovery.workflowRunId]
      : undefined
    if (exact) return exact
    const pending = Object.values(lifecycle?.activeExecutions || {}).find(
      (execution) =>
        execution.status === 'awaiting_user' &&
        (execution.pendingInteraction?.type === 'task_plan_confirmation' ||
          execution.pendingInteraction?.payload?.mode === 'build_task_plan_confirmation')
    )
    if (pending) return pending
    if (!recovery.workflowRunId || !recovery.threadId) return undefined
    const now = lifecycle?.updatedAt || new Date(0).toISOString()
    const scope = recovery.buildExecutionScope || { type: 'application', targetId: 'application' }
    return {
      scope: scope.type,
      targetId: scope.targetId || 'application',
      pageId: scope.type === 'page' ? scope.targetId : undefined,
      threadId: recovery.threadId,
      runId: recovery.workflowRunId,
      phase: 'prepare_build_tasks',
      status: 'awaiting_user',
      pendingInteraction: {
        id: `planning-refresh:${recovery.draftDigest || recovery.planningRunId || 'pending'}`,
        type: 'task_plan_confirmation',
        basedOnRevision: Math.max(1, lifecycle?.revision || 1),
        payload: recovery.confirmation || { mode: 'build_task_plan_confirmation' },
        artifactRefs: [],
        createdAt: now
      },
      startedAt: now,
      updatedAt: now
    }
  }
  return Object.values(lifecycle?.activeExecutions || {}).find(
    (execution) =>
      execution.status === 'awaiting_user' &&
      (execution.pendingInteraction?.type === 'task_plan_confirmation' ||
        execution.pendingInteraction?.payload?.mode === 'build_task_plan_confirmation')
  )
}

/** 在某个持久化会话中定位与 lifecycle execution 对应的 DAG 确认快照。 */
export function pendingDagConfirmationWorkflow(
  messages: AgentChatMessage[],
  execution: WorkbenchExecution | undefined,
  lifecycle?: ApplicationLifecycle
): WorkflowRunPayload | undefined {
  if (!execution) return undefined
  const recovery = planningRefreshState(lifecycle)
  if (
    recovery?.source === 'pending' &&
    recovery.status === 'awaiting_confirmation' &&
    recovery.confirmation
  ) {
    return {
      runId: recovery.workflowRunId || execution.runId,
      threadId: recovery.threadId || execution.threadId,
      events: [],
      summary: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        message: recovery.message,
        clarification: recovery.confirmation,
        buildTaskPlanConfirmation: recovery.confirmation,
        lifecycle
      },
      state: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        clarification: recovery.confirmation,
        lifecycle
      },
      result: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        clarification: recovery.confirmation,
        lifecycle
      }
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (
      workflow?.runId === execution.runId &&
      workflow.threadId === execution.threadId &&
      currentDagConfirmationPlan(workflow)
    ) {
      return workflow
    }
  }
  return undefined
}

/** 从当前会话最后一个 PlanningRun 读取 revision 最大的 DAG 生成快照。 */
export function latestDagGenerationSnapshot(
  messages: AgentChatMessage[],
  lifecycle?: ApplicationLifecycle
): DagGenerationSnapshot | undefined {
  let latest: DagGenerationSnapshot | undefined
  let reachedPreviousRun = false
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex]
    const steps = processStepsForDisplay(message.processSteps, message.workflow)
    if (!steps?.length) continue
    for (let stepIndex = steps.length - 1; stepIndex >= 0; stepIndex -= 1) {
      const snapshot = steps[stepIndex].dagGeneration
      if (!snapshot) continue
      if (latest && latest.planningRunId !== snapshot.planningRunId) {
        reachedPreviousRun = true
        break
      }
      latest = newerDagGenerationSnapshot(latest, snapshot)
    }
    if (reachedPreviousRun) break
  }
  const recovery = planningRefreshState(lifecycle)
  if (!recovery) return latest
  if (
    recovery.source === 'abandoned' &&
    recovery.status === 'abandoned' &&
    (!latest || latest.planningRunId === recovery.planningRunId)
  ) {
    return undefined
  }
  if (recovery.source === 'pending' || recovery.status === 'planning_run_interrupted') {
    return undefined
  }
  if (recovery.source === 'active_planning_run' && recovery.status === 'planning') {
    return readRecoveredDagGenerationSnapshot(recovery.dagGeneration) || latest
  }
  if (
    recovery.source === 'confirmed_plan' &&
    recovery.planningRunId &&
    latest?.planningRunId === recovery.planningRunId
  ) {
    return undefined
  }
  return latest
}

/** 复用 AG-UI 的严格 Snapshot parser 读取 Backend refresh 投影。 */
function readRecoveredDagGenerationSnapshot(value: unknown): DagGenerationSnapshot | undefined {
  return readDagGenerationSnapshot(value)
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

/** 读取 Backend 签发的当前 DraftIdentity，供 Abandon 精确绑定 Pending result。 */
export function currentDagConfirmationDraftIdentity(
  workflow: WorkflowRunPayload | undefined
): { planningRunId: string; draftDigest: string } | undefined {
  const value = currentDagConfirmationPayload(workflow)?.draftIdentity
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const planningRunId = String((value as Record<string, unknown>).planningRunId || '').trim()
  const draftDigest = String((value as Record<string, unknown>).draftDigest || '').trim()
  if (!planningRunId || !/^[0-9a-f]{64}$/.test(draftDigest)) return undefined
  return { planningRunId, draftDigest }
}

/** 读取当前 DAG 确认卡的结构化错误，供右侧交互卡复用原始反馈。 */
export function currentDagConfirmationErrors(workflow: WorkflowRunPayload | undefined): string[] {
  const errors = currentDagConfirmationPayload(workflow)?.errors
  return Array.isArray(errors)
    ? errors.flatMap((error) => (typeof error === 'string' && error.trim() ? [error.trim()] : []))
    : []
}

/** 从 Workflow 当前投影位置解析 DAG 确认载荷，不读取历史阶段快照。 */
function currentDagConfirmationPayload(workflow: WorkflowRunPayload | undefined):
  | {
      taskPlan?: WorkflowBuildTaskPlan
      targetReview?: WorkflowBuildTargetReview
      draftIdentity?: unknown
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
        draftIdentity?: unknown
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
  if (snapshot?.status === 'active') return 'generation'
  return 'other'
}

/** 返回 Unit 的用户可见状态；前置与复用 Unit 永不误标为生成中。 */
export function dagGenerationUnitStatusLabel(unit: DagGenerationUnitRecord): string {
  if (unit.status === 'not_required') {
    if (unit.participation === 'prerequisite_only') return 'prerequisite'
    if (unit.participation === 'reuse_only') return 'reused'
    if (unit.participation === 'structural_only') return 'structural'
  }
  return {
    not_required: 'not_required',
    pending: 'waiting',
    generating: 'generating',
    validating: 'validating',
    candidate_ready: 'candidate_ready',
    round_exhausted: 'round_exhausted',
    aborted: 'aborted'
  }[unit.status]
}

/** 仅模型生成 Unit 展示 Local attempt，确定性 Unit 不制造 0/0 次数。 */
export function dagGenerationUnitAttemptCopy(unit: DagGenerationUnitRecord): string {
  if (
    unit.generationStrategy !== 'model' ||
    unit.localAttemptLimit <= 0 ||
    unit.attemptInRound <= 0
  ) {
    return ''
  }
  return `attempt ${unit.attemptInRound}/${unit.localAttemptLimit}`
}

/** 返回复用与候选任务的安全数量，不展示 Candidate Task 身份或正文。 */
export function dagGenerationUnitTaskCopy(unit: DagGenerationUnitRecord): string {
  if (unit.retainedTaskCount === 0 && unit.candidateTaskCount === 0) return ''
  return `retained ${unit.retainedTaskCount} / candidate ${unit.candidateTaskCount}`
}

/** 返回 Unit 当前策略文案，auth 等确定性 Unit 始终明确标识 deterministic。 */
export function dagGenerationStrategyLabel(unit: DagGenerationUnitRecord): string {
  if (unit.generationStrategy === 'deterministic') return 'deterministic'
  if (unit.generationStrategy === 'prerequisite_only') return 'prerequisite'
  if (unit.generationStrategy === 'reuse_only') return 'reused'
  if (unit.generationStrategy === 'structural_only') return 'structural'
  if (unit.participation === 'reuse_and_generate') return 'reuse_and_generate'
  return 'model'
}

/** 返回 Run 级摘要；round_exhausted 只描述局部轮次，不提升为 Run 失败。 */
export function dagGenerationSummaryCopy(snapshot: DagGenerationSnapshot): string {
  if (snapshot.status === 'failed') return 'PlanningRun failed'
  if (snapshot.status === 'cancelled') return 'PlanningRun cancelled'
  if (snapshot.summary.roundExhaustedUnitCount > 0) return '本轮已耗尽，等待修复决策'
  if (snapshot.phase === 'global_check') return '正在执行 Global validation'
  if (snapshot.globalRepairRound > 0) {
    return `正在执行 Global repair ${snapshot.globalRepairRound}/${snapshot.globalRepairLimit}`
  }
  if (snapshot.summary.activeUnitCount > 0) return '正在处理 Unit'
  if (snapshot.summary.pendingUnitCount > 0) return 'Unit 等待调度'
  return 'Unit 候选已就绪，等待全局校验'
}

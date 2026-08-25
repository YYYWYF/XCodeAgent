import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowAcceptanceAdjustmentType,
  WorkflowRunPayload
} from '../../typings'
import { isConversationWorkflow } from './conversationMode'

export type PlanExecutionMode =
  | 'idle'
  | 'running'
  | 'stopping'
  | 'awaiting_authorization'
  | 'awaiting_repair_confirmation'
  | 'awaiting_unit_test_confirmation'
  | 'awaiting_frontend_performance_confirmation'
  | 'awaiting_test_phase_confirmation'
  | 'awaiting_review_phase_confirmation'
  | 'awaiting_acceptance'
  | 'awaiting_plan_adjustment'
  | 'failed'
  | 'stopped'

export type PagePlanExecutionContext = {
  execution?: WorkbenchExecution
  dependencyLocked: boolean
}

export type WorkflowInteractionAvailability = 'active' | 'stale' | 'unavailable'

/** 将验收调整类型映射到主 Workflow 的安全恢复节点。 */
export function acceptanceAdjustmentResumeNode(
  adjustmentType: WorkflowAcceptanceAdjustmentType
): string {
  if (adjustmentType === 'local_fix') return 'small_task_repair'
  if (adjustmentType === 'data_source_change') return 'entity_source_binding'
  return 'project_planning'
}

/** 根据后端权威生命周期判断历史 Workflow 确认是否仍可提交。 */
export function workflowInteractionAvailability(
  workflow: WorkflowRunPayload,
  lifecycle?: ApplicationLifecycle
): WorkflowInteractionAvailability {
  // 快速修改没有正式计划生命周期；它的确认卡由当前对话直接承接。
  if (isConversationWorkflow(workflow)) {
    return workflow.summary.status === 'requires_user_input' ? 'active' : 'stale'
  }
  const snapshotLifecycle = workflowLifecycleSnapshot(workflow)
  const snapshotExecution = snapshotLifecycle?.activeExecutions?.[workflow.runId]
  const snapshotPending = snapshotExecution?.pendingInteraction
  if (!lifecycle) return 'unavailable'
  if (!snapshotExecution || !snapshotPending) return 'stale'

  const snapshotInteractionActive =
    snapshotExecution.status === 'awaiting_user' &&
    snapshotExecution.threadId === workflow.threadId &&
    !snapshotPending.submittedAt
  const activeExecution = lifecycle.activeExecutions[workflow.runId]
  const activePending = activeExecution?.pendingInteraction
  if (!activeExecution || activeExecution.threadId !== workflow.threadId) return 'stale'
  // application-lifecycle 自定义事件和 workflow-run 卡片事件分开发送，React store
  // 可能仍保留同一 run 的 running 快照。只在该 run 尚无 pendingInteraction 且卡片
  // revision 更新时临时放行；已替换 run、已提交或令牌冲突不会进入该分支。
  if (!activePending) {
    return snapshotLifecycle.revision > lifecycle.revision && snapshotInteractionActive
      ? 'active'
      : 'stale'
  }

  return snapshotInteractionActive &&
    activeExecution.status === 'awaiting_user' &&
    activeExecution.threadId === workflow.threadId &&
    !activePending.submittedAt &&
    activePending.id === snapshotPending.id &&
    activePending.basedOnRevision === snapshotPending.basedOnRevision
    ? 'active'
    : 'stale'
}

/** 从 Workflow 的兼容投影位置读取提交交互所依据的生命周期快照。 */
function workflowLifecycleSnapshot(workflow: WorkflowRunPayload): ApplicationLifecycle | undefined {
  // 节点边界先在 summary 广播最新生命周期，再生成 state/result；优先读取 summary
  // 可避免当前确认卡因帧间时序被误判为历史卡，同时仍校验交互 id 与 revision。
  const candidates = [
    workflow.summary.lifecycle,
    workflow.state?.lifecycle,
    workflow.result?.lifecycle
  ]
  return candidates.find((candidate): candidate is ApplicationLifecycle =>
    Boolean(candidate && typeof candidate === 'object')
  )
}

/** 从后端权威 execution 派生持久模式，停止中的短暂反馈由 Workflow 状态覆盖。 */
export function derivePlanExecutionMode(execution?: WorkbenchExecution): PlanExecutionMode {
  if (!execution || execution.status === 'completed') return 'idle'
  if (execution.status === 'running') return 'running'
  if (execution.status === 'stopping') return 'stopping'
  if (execution.status === 'failed') return 'failed'
  if (execution.status === 'stopped') return 'stopped'

  const interactionType = execution.pendingInteraction?.type
  if (interactionType === 'agent_approval') return 'awaiting_authorization'
  if (interactionType === 'repair_scope_confirmation') {
    return 'awaiting_repair_confirmation'
  }
  if (interactionType === 'unit_test_confirmation') {
    return 'awaiting_unit_test_confirmation'
  }
  if (interactionType === 'frontend_performance_confirmation') {
    return 'awaiting_frontend_performance_confirmation'
  }
  if (interactionType === 'test_phase_confirmation') {
    return 'awaiting_test_phase_confirmation'
  }
  if (interactionType === 'review_phase_confirmation') {
    return 'awaiting_review_phase_confirmation'
  }
  if (interactionType === 'page_acceptance') return 'awaiting_acceptance'
  return 'awaiting_plan_adjustment'
}

/** 在生命周期快照暂缺时用当前 Workflow 状态守住输入锁，只有明确终态才恢复自由输入。 */
export function deriveDisplayedPlanExecutionMode(
  execution: WorkbenchExecution | undefined,
  workflowStatus: string | undefined,
  requestRunning: boolean,
  hasLifecycleSnapshot = false
): PlanExecutionMode {
  if (requestRunning && workflowStatus === 'stopping') return 'stopping'
  if (
    (!hasLifecycleSnapshot || execution) &&
    (workflowStatus === 'stopped' || workflowStatus === 'cancelled')
  ) {
    return 'stopped'
  }
  const executionMode = derivePlanExecutionMode(execution)
  if (executionMode !== 'idle') return executionMode
  if (requestRunning) {
    return 'running'
  }
  // 已加载的应用生命周期是当前锁权威；其中没有 execution 时，历史会话状态不能重新锁住输入框。
  if (hasLifecycleSnapshot) return 'idle'
  if (workflowStatus === 'stopping') return 'stopping'
  if (workflowStatus === 'running' || workflowStatus === 'requires_user_input') return 'running'
  if (workflowStatus === 'failed') return 'failed'
  return 'idle'
}

/** 自由对话期间隐藏计划控制栏，仅让正式 Workflow 占用底部控制栏。 */
export function shouldRenderPlanExecutionDock(
  mode: PlanExecutionMode,
  conversationActive: boolean
): mode is Exclude<PlanExecutionMode, 'idle'> {
  return mode !== 'idle' && !conversationActive
}

/** 乐观更新指定计划执行的控制状态，不覆盖独立的创建生命周期状态。 */
export function withWorkflowExecutionStatus(
  workflow: WorkflowRunPayload | undefined,
  status: 'stopping' | 'stopped',
  targetRunId?: string
): WorkflowRunPayload | undefined {
  if (!workflow) return workflow
  const lifecycle = workflow.summary.lifecycle
  const executionRunId = targetRunId || workflow.runId
  const execution = lifecycle?.activeExecutions?.[executionRunId]
  const nextLifecycle =
    lifecycle && execution
      ? {
          ...lifecycle,
          activeExecutions: {
            ...lifecycle.activeExecutions,
            [execution.runId]: { ...execution, status }
          }
        }
      : lifecycle
  return {
    ...workflow,
    summary: { ...workflow.summary, status, lifecycle: nextLifecycle },
    state: nextLifecycle ? { ...workflow.state, lifecycle: nextLifecycle } : workflow.state,
    result: nextLifecycle ? { ...workflow.result, lifecycle: nextLifecycle } : workflow.result
  }
}

/** 只返回当前页面或当前 Workflow 自己的执行；资源登记不参与输入门禁。 */
export function planExecutionForPage(
  lifecycle: ApplicationLifecycle | undefined,
  pageId: string | undefined,
  workflowIdentity?: { runId?: string; threadId?: string }
): WorkbenchExecution | undefined {
  return planExecutionContextForPage(lifecycle, pageId, workflowIdentity).execution
}

/** 返回当前页面或当前 Workflow 自己的执行，不读取 resourceLocks 判定占用。 */
export function planExecutionContextForPage(
  lifecycle: ApplicationLifecycle | undefined,
  pageId: string | undefined,
  workflowIdentity?: { runId?: string; threadId?: string }
): PagePlanExecutionContext {
  const executions = Object.values(lifecycle?.activeExecutions || {})
  const runIdentityExecution = workflowIdentity?.runId
    ? lifecycle?.activeExecutions?.[workflowIdentity.runId]
    : undefined
  if (runIdentityExecution) {
    return { execution: runIdentityExecution, dependencyLocked: false }
  }
  const normalizedPageId = normalizePageId(pageId)
  const applicationExecution = executions.find((execution) => execution.scope === 'application')
  if (!normalizedPageId && applicationExecution) {
    return {
      execution: applicationExecution,
      dependencyLocked: false
    }
  }
  const pageExecution = normalizedPageId
    ? executions.find(
        (execution) =>
          execution.scope === 'page' &&
          [execution.pageId, execution.targetId].some(
            (candidate) => normalizePageId(candidate) === normalizedPageId
          )
      )
    : undefined
  if (pageExecution) {
    return {
      execution: pageExecution,
      dependencyLocked: false
    }
  }

  // 恢复运行可能把历史 pageId 规范化为新值；同一 Workflow 身份是页面隔离内更稳定的兜底键。
  const identityExecution = executions.find(
    (execution) =>
      (Boolean(workflowIdentity?.runId) && execution.runId === workflowIdentity?.runId) ||
      (Boolean(workflowIdentity?.threadId) && execution.threadId === workflowIdentity?.threadId)
  )
  return { execution: identityExecution, dependencyLocked: false }
}

/** 返回当前 endpoint 或当前 Workflow 自己的持久化执行状态。 */
export function planExecutionContextForEndpoint(
  lifecycle: ApplicationLifecycle | undefined,
  apiContractId: string | undefined,
  endpointId: string | undefined,
  workflowIdentity?: { runId?: string; threadId?: string }
): PagePlanExecutionContext {
  const executions = Object.values(lifecycle?.activeExecutions || {})
  const runIdentityExecution = workflowIdentity?.runId
    ? lifecycle?.activeExecutions?.[workflowIdentity.runId]
    : undefined
  if (runIdentityExecution) {
    return { execution: runIdentityExecution, dependencyLocked: false }
  }
  const normalizedContractId = String(apiContractId || '').trim()
  const normalizedEndpointId = String(endpointId || '').trim()
  const resourceKey =
    normalizedContractId && normalizedEndpointId
      ? `endpoint:${normalizedContractId}:${normalizedEndpointId}`
      : ''
  const endpointExecution = executions.find((execution) => {
    if (execution.scope !== 'endpoint') return false
    const endpointResourceKeys = (execution.resourceKeys || []).filter((key) =>
      key.startsWith('endpoint:')
    )
    if (resourceKey && endpointResourceKeys.length) {
      return endpointResourceKeys.includes(resourceKey)
    }
    return execution.targetId === normalizedEndpointId
  })
  if (endpointExecution) {
    return { execution: endpointExecution, dependencyLocked: false }
  }
  const identityExecution = executions.find(
    (execution) =>
      (Boolean(workflowIdentity?.runId) && execution.runId === workflowIdentity?.runId) ||
      (Boolean(workflowIdentity?.threadId) && execution.threadId === workflowIdentity?.threadId)
  )
  return { execution: identityExecution, dependencyLocked: false }
}

/** 只按当前 Workflow 自身 runId/threadId 定位执行，不回退到应用级或页面级执行。 */
export function planExecutionContextForRun(
  lifecycle: ApplicationLifecycle | undefined,
  workflowIdentity?: { runId?: string; threadId?: string }
): PagePlanExecutionContext {
  const executions = Object.values(lifecycle?.activeExecutions || {})
  const runIdentityExecution = workflowIdentity?.runId
    ? lifecycle?.activeExecutions?.[workflowIdentity.runId]
    : undefined
  if (runIdentityExecution) {
    return { execution: runIdentityExecution, dependencyLocked: false }
  }
  const identityExecution = executions.find(
    (execution) =>
      (Boolean(workflowIdentity?.runId) && execution.runId === workflowIdentity?.runId) ||
      (Boolean(workflowIdentity?.threadId) && execution.threadId === workflowIdentity?.threadId)
  )
  return { execution: identityExecution, dependencyLocked: false }
}

/** 统一页面标识的历史前缀与分隔符，避免同一页面被误判为空闲。 */
function normalizePageId(value?: string): string {
  return (value || '')
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/^page-/, '')
}

/** 把内部 Workflow 节点转换为底部锁定条可读的当前任务名称。 */
export function planExecutionPhaseLabel(phase?: string): string {
  return (
    {
      development_readiness_gate: '检查开发前置',
      entity_source_binding: '实体数据源绑定',
      inspect_workspace: '检查工作区',
      inspect_database_context: '获取数据库信息',
      prepare_build_tasks: '生成执行计划',
      build: '开发实现',
      unit_test: '开发阶段单元测试',
      unit_test_repair: '单元测试局部修复',
      test_phase_confirmation: '开发完成确认',
      integration_test: '集成测试',
      small_task_repair: '执行局部修复任务',
      review_phase_confirmation: '审查阶段确认',
      code_review: '前后端代码审查',
      launch_project: '启动预览',
      acceptance: '预览验收',
      finalize_project: '完成交付'
    }[phase || ''] || '执行页面计划'
  )
}

/** 判断当前计划模式是否允许显示节点级调试恢复入口。 */
export function planExecutionShowsDebugResume(mode: PlanExecutionMode): boolean {
  return mode === 'stopped' || mode === 'awaiting_plan_adjustment'
}

/** 从最近执行事件和生命周期阶段推断最安全的 Workflow 恢复节点。 */
export function workflowResumeNode(
  workflow: WorkflowRunPayload | undefined,
  executionPhase?: string
): string {
  const supported = new Set([
    'development_readiness_gate',
    'entity_source_binding',
    'inspect_workspace',
    'inspect_database_context',
    'prepare_build_tasks',
    'build',
    'unit_test',
    'unit_test_repair',
    'test_phase_confirmation',
    'integration_test',
    'small_task_repair',
    'review_phase_confirmation',
    'code_review',
    'launch_project',
    'acceptance',
    'finalize_project'
  ])
  const events = workflow?.events || []
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const nodeName = events[index].nodeName || events[index].node?.id
    if (nodeName && supported.has(nodeName)) return nodeName
  }
  const phase = String(workflow?.summary.phase || '')
  if (supported.has(phase)) return phase
  return executionPhase && supported.has(executionPhase) ? executionPhase : 'build'
}

/** 判断当前 Workflow 或生命周期 execution 是否存在可由显式动作恢复的 Build 失败。 */
export function workflowCanRetryFailedTasks(
  workflow?: WorkflowRunPayload,
  execution?: WorkbenchExecution
): boolean {
  // 失败时生命周期是后端权威来源；即使历史 Workflow 快照尚未带回修复计划，也不能隐藏恢复入口。
  if (execution?.status === 'failed' && execution.error?.recoverable === true) return true

  const candidates: unknown[] = [
    workflow?.summary?.buildSummary,
    workflow?.summary?.build_summary,
    workflow?.state?.buildSummary,
    workflow?.state?.build_summary,
    workflow?.result?.build_summary,
    workflow?.result?.buildSummary
  ]
  const summary = candidates.find((candidate): candidate is Record<string, unknown> => {
    if (!candidate || typeof candidate !== 'object') return false
    return [
      'recovery_available',
      'recoveryAvailable',
      'retry_available',
      'retryAvailable',
      'retryable_failures',
      'retryableFailures'
    ].some((key) => key in candidate)
  })
  if (!summary) return workflowHasReadyRepairPlan(workflow)
  if (typeof summary.recovery_available === 'boolean') {
    return summary.recovery_available || workflowHasReadyRepairPlan(workflow)
  }
  if (typeof summary.recoveryAvailable === 'boolean') {
    return summary.recoveryAvailable || workflowHasReadyRepairPlan(workflow)
  }
  if (typeof summary.retry_available === 'boolean') {
    return summary.retry_available || workflowHasReadyRepairPlan(workflow)
  }
  if (typeof summary.retryAvailable === 'boolean') {
    return summary.retryAvailable || workflowHasReadyRepairPlan(workflow)
  }
  const retryable = Number(summary.retryable_failures ?? summary.retryableFailures ?? 0)
  const repairable = Number(summary.repairable_failures ?? summary.repairableFailures ?? 0)
  const confirmation = Number(summary.requires_confirmation ?? summary.requiresConfirmation ?? 0)
  return (
    workflowHasReadyRepairPlan(workflow) ||
    (retryable > 0 && repairable === 0 && confirmation === 0)
  )
}

/** 从 Workflow 快照识别已生成且仍有待执行任务的 RepairPlanner 计划。 */
function workflowHasReadyRepairPlan(workflow?: WorkflowRunPayload): boolean {
  const candidates: unknown[] = [
    workflow?.summary?.repairTaskPlan,
    workflow?.summary?.repair_task_plan,
    workflow?.state?.repairTaskPlan,
    workflow?.state?.repair_task_plan,
    workflow?.result?.repairTaskPlan,
    workflow?.result?.repair_task_plan
  ]
  return candidates.some((candidate) => {
    if (!candidate || typeof candidate !== 'object') return false
    const plan = candidate as Record<string, unknown>
    if (plan.decision && plan.decision !== 'repair') return false
    if (plan.status && !['ready', 'pending', 'in_progress'].includes(String(plan.status))) {
      return false
    }
    const tasks = Array.isArray(plan.tasks) ? plan.tasks : []
    return tasks.some((task) => {
      if (!task || typeof task !== 'object') return false
      const status = String((task as Record<string, unknown>).status || 'pending')
      return !['completed', 'already_satisfied'].includes(status)
    })
  })
}

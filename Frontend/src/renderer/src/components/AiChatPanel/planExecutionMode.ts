import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowRunPayload
} from '../../typings'

export type PlanExecutionMode =
  | 'idle'
  | 'running'
  | 'stopping'
  | 'awaiting_authorization'
  | 'awaiting_repair_confirmation'
  | 'awaiting_acceptance'
  | 'awaiting_plan_adjustment'
  | 'failed'
  | 'stopped'

export type PagePlanExecutionContext = {
  execution?: WorkbenchExecution
  dependencyLocked: boolean
}

export type WorkflowInteractionAvailability = 'active' | 'stale' | 'unavailable'

/** 根据后端权威生命周期判断历史 Workflow 确认是否仍可提交。 */
export function workflowInteractionAvailability(
  workflow: WorkflowRunPayload,
  lifecycle?: ApplicationLifecycle
): WorkflowInteractionAvailability {
  if (!lifecycle) return 'unavailable'

  const snapshotExecution =
    workflowLifecycleSnapshot(workflow)?.activeExecutions?.[workflow.runId]
  const snapshotPending = snapshotExecution?.pendingInteraction
  const activeExecution = lifecycle.activeExecutions[workflow.runId]
  const activePending = activeExecution?.pendingInteraction
  if (!snapshotPending || !activePending) return 'stale'

  return activeExecution.status === 'awaiting_user' &&
    activeExecution.threadId === workflow.threadId &&
    !activePending.submittedAt &&
    activePending.id === snapshotPending.id &&
    activePending.basedOnRevision === snapshotPending.basedOnRevision
    ? 'active'
    : 'stale'
}

/** 从 Workflow 的兼容投影位置读取提交交互所依据的生命周期快照。 */
function workflowLifecycleSnapshot(
  workflow: WorkflowRunPayload
): ApplicationLifecycle | undefined {
  const candidates = [workflow.state?.lifecycle, workflow.result?.lifecycle]
  return candidates.find(
    (candidate): candidate is ApplicationLifecycle =>
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
  const normalizedContractId = String(apiContractId || '').trim()
  const normalizedEndpointId = String(endpointId || '').trim()
  const resourceKey =
    normalizedContractId && normalizedEndpointId
      ? `endpoint:${normalizedContractId}:${normalizedEndpointId}`
      : ''
  const endpointExecution = executions.find(
    (execution) => {
      if (execution.scope !== 'endpoint') return false
      const endpointResourceKeys = (execution.resourceKeys || []).filter((key) =>
        key.startsWith('endpoint:')
      )
      if (resourceKey && endpointResourceKeys.length) {
        return endpointResourceKeys.includes(resourceKey)
      }
      return execution.targetId === normalizedEndpointId
    }
  )
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
      detail_confirmation: '确认页面设计',
      inspect_workspace: '检查工作区',
      inspect_database_context: '获取数据库信息',
      prepare_build_tasks: '生成执行计划',
      build: '开发实现',
      integration_test: '集成测试',
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
    'detail_confirmation',
    'inspect_workspace',
    'inspect_database_context',
    'prepare_build_tasks',
    'build',
    'integration_test',
    'launch_project',
    'acceptance'
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

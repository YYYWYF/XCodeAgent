import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowRunPayload
} from '../../typings'
import { isInitialPlanningPhase } from '../../workbenchPhase'

export type WorkflowInteractionAvailability = 'active' | 'stale' | 'unavailable'

/** 根据后端权威生命周期判断历史 Workflow 确认是否仍可提交。 */
export function workflowInteractionAvailability(
  workflow: WorkflowRunPayload,
  lifecycle?: ApplicationLifecycle
): WorkflowInteractionAvailability {
  if (!lifecycle) return 'unavailable'

  // 应用规划(需求分析/项目规划阶段)确认没有 execution 锁：workflow 待用户输入且应用仍在规划期时视为
  // 当前可交互(active)，避免需求分析/项目规划阶段的澄清/需求/计划确认卡被 execution 锁判定误杀为 stale。
  // 推进到开发期(isInitialPlanningPhase=false)后，旧规划卡自动回 stale 不再显示。
  if (workflow.summary.status === 'requires_user_input' && isInitialPlanningPhase(lifecycle)) {
    return 'active'
  }

  const snapshotExecution =
    workflowLifecycleSnapshot(workflow)?.activeExecutions?.[workflow.runId]
  const snapshotPending = snapshotExecution?.pendingInteraction
  const activeExecution = lifecycle.activeExecutions[workflow.runId]
  const activePending = activeExecution?.pendingInteraction

  // 测试用例授权是当前用例 Workflow 的启动动作。旧快照可能还没有写入
  // pendingInteraction，但只要同一条 application execution 仍在等待用户，
  // 当前最后一张授权卡就必须保持可操作，不能误判为失效。
  const workflowState = (workflow.state || {}) as Record<string, unknown>
  const clarificationState = workflowState.clarification
  const isTestCaseAuthorization =
    workflowState.testWorkflowType === 'case' &&
    clarificationState &&
    typeof clarificationState === 'object' &&
    (clarificationState as { mode?: unknown }).mode === 'test_case_execute'
  // 产物验收同理：验收工作流的确认卡挂在同一条 execution 的等待态上，
  // execution 仍在等待用户时验收卡保持可操作。
  // 后台执行方式选择卡也是启动动作：挂起期间用户必须能点选算力类型。
  const isArtifactAcceptanceInteraction =
    clarificationState &&
    typeof clarificationState === 'object' &&
    (clarificationState as { mode?: unknown }).mode === 'page_acceptance'
  const isBackgroundDispatchInteraction =
    clarificationState &&
    typeof clarificationState === 'object' &&
    (clarificationState as { mode?: unknown }).mode === 'background_dispatch'
  if (
    (isTestCaseAuthorization || isArtifactAcceptanceInteraction || isBackgroundDispatchInteraction) &&
    activeExecution?.status === 'awaiting_user' &&
    activeExecution.threadId === workflow.threadId &&
    activeExecution.runId === workflow.runId &&
    !activePending?.submittedAt
  ) {
    return 'active'
  }
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
  const executions = Object.values(lifecycle?.activeExecutions || {})
  const normalizedPageId = normalizePageId(pageId)
  const applicationExecution = executions.find((execution) => execution.scope === 'application')
  if (!normalizedPageId && applicationExecution) {
    return applicationExecution
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
    return pageExecution
  }

  // 恢复运行可能把历史 pageId 规范化为新值；同一 Workflow 身份是页面隔离内更稳定的兜底键。
  return executions.find(
    (execution) =>
      (Boolean(workflowIdentity?.runId) && execution.runId === workflowIdentity?.runId) ||
      (Boolean(workflowIdentity?.threadId) && execution.threadId === workflowIdentity?.threadId)
  )
}

/** 统一页面标识的历史前缀与分隔符，避免同一页面被误判为空闲。 */
function normalizePageId(value?: string): string {
  return (value || '')
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/^page-/, '')
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

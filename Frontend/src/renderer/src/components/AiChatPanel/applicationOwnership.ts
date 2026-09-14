import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkbenchExecutionStatus,
  WorkflowRunPayload
} from '../../typings'
import type { ChatSessionSummary } from '../../service/chatSessions'
import type { WorkbenchPhase } from '../../workbenchPhase'
import {
  isDagPlanningPhase,
  type SessionExecutionEntry,
  type SessionIdentity
} from './hooks/sessionRuntime'

export type ApplicationMutationOwner = {
  sessionId?: string
  threadId?: string
  title?: string
  workbenchPhase?: WorkbenchPhase
  status: string
  runId?: string
  source: 'active_dag_execution' | 'active_planning_run' | 'pending_plan'
}

export type ApplicationMutationOwnership = {
  state: 'free' | 'owned' | 'conflicted' | 'invalid_pending_owner'
  owner?: ApplicationMutationOwner
  actionablePending: boolean
}

export type CurrentWorkflowInteraction = {
  mode: string
  status?: string
  source: 'current_clarification' | 'historical_dag_confirmation'
}

type OwnershipCandidate = ApplicationMutationOwner & {
  updatedAt: string
}

type ApplicationOwnerSession = Pick<
  ChatSessionSummary,
  'id' | 'title' | 'threadId' | 'workbenchPhase'
>

type ApplicationOwnershipScope = {
  applicationId?: string
  workspaceRoot?: string
}

const ACTIVE_DAG_EXECUTION_STATUSES = new Set<WorkbenchExecutionStatus>(['running', 'stopping'])
const ACTIVE_LOCAL_DAG_EXECUTION_STATUSES = new Set<SessionExecutionEntry['status']>([
  'starting',
  'running',
  'stopping'
])

/** 从当前工作区会话列表中补齐 owner 的可展示身份。 */
function sessionForOwner(
  sessions: readonly ApplicationOwnerSession[],
  sessionId?: string,
  threadId?: string
): ApplicationOwnerSession | undefined {
  return sessions.find(
    (session) =>
      (sessionId && session.id === sessionId) || (threadId && session.threadId === threadId)
  )
}

/** 只把当前支持的工作台阶段投影给锁提示，未知阶段不伪造阶段身份。 */
function workbenchPhaseOf(value: unknown): WorkbenchPhase | undefined {
  if (value === 'prepare_build_tasks') return 'development'
  return value === 'product' ||
    value === 'planning' ||
    value === 'development' ||
    value === 'test' ||
    value === 'review' ||
    value === 'acceptance'
    ? value
    : undefined
}

/** 只从 DAG Planning 生命周期 execution 构造 owner 候选，不读取资源锁推断归属。 */
function executionCandidate(
  execution: WorkbenchExecution,
  sessions: readonly ApplicationOwnerSession[]
): OwnershipCandidate {
  const threadId = String(execution.threadId || '').trim() || undefined
  const session = sessionForOwner(sessions, undefined, threadId)
  return {
    sessionId: session?.id,
    threadId,
    title: session?.title,
    workbenchPhase: session?.workbenchPhase || workbenchPhaseOf(execution.phase),
    status: execution.status,
    runId: execution.runId,
    source: 'active_dag_execution',
    updatedAt: execution.updatedAt || execution.startedAt || ''
  }
}

/** 从前端本地 DAG Planning 登记构造 owner 候选，使 AG-UI 首帧前也能保护其它会话。 */
function localExecutionCandidate(
  entry: SessionExecutionEntry,
  sessions: readonly ApplicationOwnerSession[]
): OwnershipCandidate {
  const session = sessionForOwner(sessions, entry.identity.sessionId, entry.identity.threadId)
  return {
    sessionId: entry.identity.sessionId,
    threadId: entry.identity.threadId,
    title: session?.title,
    workbenchPhase: entry.identity.workbenchPhase,
    status: entry.status,
    source: 'active_dag_execution',
    updatedAt: ''
  }
}

/** 合并同一 thread 的持久化与本地候选，避免 lifecycle 和实时登记重复形成冲突。 */
function mergeOwnershipCandidates(candidates: OwnershipCandidate[]): OwnershipCandidate[] {
  const merged = new Map<string, OwnershipCandidate>()
  candidates.forEach((candidate, index) => {
    const key = candidate.threadId || `missing:${candidate.runId || index}`
    const previous = merged.get(key)
    if (!previous) {
      merged.set(key, candidate)
      return
    }
    const preferred = candidate.updatedAt >= previous.updatedAt ? candidate : previous
    merged.set(key, {
      ...preferred,
      sessionId: preferred.sessionId || previous.sessionId || candidate.sessionId,
      threadId: preferred.threadId || previous.threadId || candidate.threadId,
      title: preferred.title || previous.title || candidate.title,
      workbenchPhase:
        preferred.workbenchPhase || previous.workbenchPhase || candidate.workbenchPhase
    })
  })
  return [...merged.values()]
}

/** 从 DAG Planning 状态和本地登记派生 owner，普通 Workbench execution 永不参与。 */
export function resolveApplicationMutationOwnership(
  lifecycle?: ApplicationLifecycle,
  sessions: readonly ApplicationOwnerSession[] = [],
  localExecutions: readonly SessionExecutionEntry[] = [],
  scope?: ApplicationOwnershipScope
): ApplicationMutationOwnership {
  const refresh = lifecycle?.extensions?.planningRefresh
  const actionablePending = Boolean(
    refresh?.schemaVersion === 'planning-refresh.v1' &&
      refresh.source === 'pending_plan' &&
      refresh.status === 'awaiting_confirmation'
  )
  if (actionablePending) {
    const ownerSessionId = String(refresh?.ownerSessionId || '').trim()
    // PendingPlan 缺少 owner 时只报告非法投影，不能把所有历史会话变成 readonly。
    if (!ownerSessionId) {
      return { state: 'invalid_pending_owner', actionablePending: true }
    }
    const session = sessionForOwner(sessions, ownerSessionId, refresh?.threadId)
    return {
      state: 'owned',
      actionablePending: true,
      owner: {
        sessionId: ownerSessionId,
        threadId: String(refresh?.threadId || session?.threadId || '').trim() || undefined,
        title: session?.title,
        workbenchPhase: session?.workbenchPhase,
        status: 'awaiting_confirmation',
        runId: refresh?.workflowRunId,
        source: 'pending_plan'
      }
    }
  }

  const candidates: OwnershipCandidate[] = []
  Object.values(lifecycle?.activeExecutions || {}).forEach((execution) => {
    // 只有 prepare_build_tasks 的 running/stopping 代表 DAG generation 或 Regenerate。
    // awaiting_user 必须由上面的 actionable PendingPlan 单独确认，不能靠 execution 猜测。
    if (
      isDagPlanningPhase(execution.phase) &&
      ACTIVE_DAG_EXECUTION_STATUSES.has(execution.status)
    ) {
      candidates.push(executionCandidate(execution, sessions))
    }
  })
  localExecutions.forEach((entry) => {
    if (
      entry.identity.workflowId === (scope?.applicationId || lifecycle?.application.id) &&
      entry.identity.workspaceRoot &&
      (!scope?.workspaceRoot || entry.identity.workspaceRoot === scope.workspaceRoot) &&
      isDagPlanningPhase(entry.phase) &&
      ACTIVE_LOCAL_DAG_EXECUTION_STATUSES.has(entry.status)
    ) {
      candidates.push(localExecutionCandidate(entry, sessions))
    }
  })

  const planningRun =
    refresh?.schemaVersion === 'planning-refresh.v1' &&
    refresh.source === 'active_planning_run' &&
    refresh.status === 'planning'
      ? refresh
      : undefined
  if (planningRun) {
    const threadId = String(planningRun.threadId || '').trim() || undefined
    const session = sessionForOwner(sessions, undefined, threadId)
    candidates.push({
      sessionId: session?.id,
      threadId,
      title: session?.title,
      workbenchPhase: session?.workbenchPhase,
      status: 'running',
      runId: planningRun.workflowRunId,
      source: 'active_planning_run',
      updatedAt: ''
    })
  }

  const owners = mergeOwnershipCandidates(candidates)
  if (owners.length === 0) return { state: 'free', actionablePending: false }
  // DAG 活动状态出现多个无法归并的 thread 时 fail closed，避免任意一个会话误获写权限。
  if (owners.some((candidate) => !candidate.threadId) || owners.length > 1) {
    return { state: 'conflicted', actionablePending: false }
  }
  const owner = owners[0]
  const { updatedAt, ...publicOwner } = owner
  void updatedAt
  return { state: 'owned', owner: publicOwner, actionablePending: false }
}

/** 判断当前会话是否因另一 owner 或无法归并的活动执行而进入 Application readonly。 */
export function applicationMutationReadonlyForSession(
  ownership: ApplicationMutationOwnership,
  identity?: Pick<SessionIdentity, 'sessionId' | 'threadId'>
): boolean {
  if (ownership.state === 'conflicted') return true
  if (ownership.state !== 'owned' || !ownership.owner) return false
  const sameSession = Boolean(
    identity && ownership.owner.sessionId && identity.sessionId === ownership.owner.sessionId
  )
  const sameThread = Boolean(
    identity && ownership.owner.threadId && identity.threadId === ownership.owner.threadId
  )
  return !sameSession && !sameThread
}

/** 从 Workflow 投影中读取当前交互，只有当前 clarification 缺失时才回退历史 DAG。 */
export function currentWorkflowInteraction(
  workflow: WorkflowRunPayload
): CurrentWorkflowInteraction | undefined {
  const currentCandidates: unknown[] = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification,
    workflow.summary.reviewPhaseConfirmation,
    workflow.summary.acceptancePhaseConfirmation
  ]
  const current = currentCandidates
    .map(readWorkflowInteraction)
    .find((interaction): interaction is Omit<CurrentWorkflowInteraction, 'source'> =>
      Boolean(interaction)
    )
  if (current) return { ...current, source: 'current_clarification' }

  const historical = readWorkflowInteraction(workflow.summary.buildTaskPlanConfirmation)
  return historical?.mode === 'build_task_plan_confirmation'
    ? { ...historical, source: 'historical_dag_confirmation' }
    : undefined
}

/** 从未知投影值中读取带 mode 的交互载荷。 */
function readWorkflowInteraction(
  value: unknown
): Omit<CurrentWorkflowInteraction, 'source'> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  const mode = String(record.mode || '').trim()
  return mode ? { mode, status: String(record.status || '').trim() || undefined } : undefined
}

import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkbenchExecutionStatus
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
  source: 'active_dag_execution' | 'pending_plan'
}

export type PendingPlanOwnership = {
  ownerSessionId?: string
  planningRunId?: string
  workflowRunId?: string
}

export type ApplicationMutationOwnership = {
  state: 'free' | 'owned' | 'conflicted' | 'invalid_pending_owner'
  owner?: ApplicationMutationOwner
  actionablePending: boolean
  /** 保留当前 Pending 的最小身份，供 UI 锁定和诊断共用同一选择结果。 */
  pendingPlan?: PendingPlanOwnership
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
  const pendingPlan = actionablePending
    ? {
        ownerSessionId: String(refresh?.ownerSessionId || '').trim() || undefined,
        planningRunId: String(refresh?.planningRunId || '').trim() || undefined,
        workflowRunId: String(refresh?.workflowRunId || '').trim() || undefined
      }
    : undefined
  if (actionablePending) {
    const ownerSessionId = pendingPlan?.ownerSessionId
    // PendingPlan 缺少 owner 时只报告非法投影，不能把所有历史会话变成 readonly。
    if (!ownerSessionId) {
      return { state: 'invalid_pending_owner', actionablePending: true, pendingPlan }
    }
    const session = sessionForOwner(sessions, ownerSessionId)
    return {
      state: 'owned',
      actionablePending: true,
      pendingPlan,
      owner: {
        sessionId: ownerSessionId,
        threadId: session?.threadId,
        title: session?.title,
        workbenchPhase: session?.workbenchPhase,
        status: 'awaiting_confirmation',
        runId: pendingPlan?.workflowRunId,
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

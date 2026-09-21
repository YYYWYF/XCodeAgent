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
  identitySource: OwnershipIdentitySource
  identityConflict: boolean
}

type OwnershipIdentitySource =
  | 'owner_session_id'
  | 'local_execution'
  | 'visible_session_thread'
  | 'unresolved'

type LocalExecutionIdentityEvidence = {
  sessionId?: string
  conflicted: boolean
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
const ACTIVE_LOCAL_EXECUTION_STATUSES = new Set<SessionExecutionEntry['status']>([
  'starting',
  'running',
  'stopping'
])

const OWNERSHIP_IDENTITY_PRIORITY: Record<OwnershipIdentitySource, number> = {
  owner_session_id: 4,
  local_execution: 3,
  visible_session_thread: 2,
  unresolved: 1
}

/** 判断本地 execution 是否属于当前 application/workspace，避免跨应用串联身份证据。 */
function localExecutionBelongsToScope(
  entry: SessionExecutionEntry,
  lifecycle: ApplicationLifecycle | undefined,
  scope: ApplicationOwnershipScope | undefined
): boolean {
  const applicationId = scope?.applicationId || lifecycle?.application.id
  return Boolean(
    applicationId &&
      entry.identity.workflowId === applicationId &&
      entry.identity.workspaceRoot &&
      (!scope?.workspaceRoot || entry.identity.workspaceRoot === scope.workspaceRoot)
  )
}

/** 从所有 active 本地 execution 建立 executionThreadId 到 sessionId 的身份证据。 */
function buildLocalExecutionIdentityEvidence(
  lifecycle: ApplicationLifecycle | undefined,
  localExecutions: readonly SessionExecutionEntry[],
  scope: ApplicationOwnershipScope | undefined
): Map<string, LocalExecutionIdentityEvidence> {
  const evidence = new Map<string, LocalExecutionIdentityEvidence>()
  localExecutions.forEach((entry) => {
    if (
      !ACTIVE_LOCAL_EXECUTION_STATUSES.has(entry.status) ||
      !localExecutionBelongsToScope(entry, lifecycle, scope)
    ) {
      return
    }
    const executionThreadId = String(entry.executionThreadId || '').trim()
    if (!executionThreadId) return
    const previous = evidence.get(executionThreadId)
    if (!previous) {
      evidence.set(executionThreadId, {
        sessionId: entry.identity.sessionId,
        conflicted: false
      })
      return
    }
    if (previous.conflicted || previous.sessionId === entry.identity.sessionId) return
    // 同一真实 execution thread 被两个 active session 声明时不能猜 owner。
    evidence.set(executionThreadId, { conflicted: true })
  })
  return evidence
}

/** 判断当前可见 session 是否仍有 active runtime entry，供 LockDock 渲染安全兜底使用。 */
export function hasActiveSessionExecution(
  localExecutions: readonly SessionExecutionEntry[],
  identity?: Pick<SessionIdentity, 'key'>
): boolean {
  return Boolean(
    identity &&
      localExecutions.some(
        (entry) =>
          entry.identity.key === identity.key && ACTIVE_LOCAL_EXECUTION_STATUSES.has(entry.status)
      )
  )
}

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
  sessions: readonly ApplicationOwnerSession[],
  localIdentityEvidence: Map<string, LocalExecutionIdentityEvidence>
): OwnershipCandidate {
  const threadId = String(execution.threadId || '').trim() || undefined
  const explicitOwnerSessionId = String(execution.ownerSessionId || '').trim() || undefined
  const localEvidence = threadId ? localIdentityEvidence.get(threadId) : undefined
  const localOwnerSessionId = localEvidence?.sessionId
  const correlatedOwnerSessionId = explicitOwnerSessionId || localOwnerSessionId
  // ownerSessionId 和本地 execution correlation 是稳定身份；只有两者都缺失时才允许
  // 用可见 Chat Session 的 thread 做最后一级兼容 fallback。
  const fallbackSession =
    !correlatedOwnerSessionId && !localEvidence?.conflicted
      ? sessionForOwner(sessions, undefined, threadId)
      : undefined
  const session = correlatedOwnerSessionId
    ? sessionForOwner(sessions, correlatedOwnerSessionId)
    : fallbackSession
  const identitySource: OwnershipIdentitySource = explicitOwnerSessionId
    ? 'owner_session_id'
    : localOwnerSessionId
      ? 'local_execution'
      : fallbackSession
        ? 'visible_session_thread'
        : 'unresolved'
  return {
    sessionId: correlatedOwnerSessionId || fallbackSession?.id,
    threadId,
    title: session?.title,
    workbenchPhase: session?.workbenchPhase || workbenchPhaseOf(execution.phase),
    status: execution.status,
    runId: execution.runId,
    source: 'active_dag_execution',
    updatedAt: execution.updatedAt || execution.startedAt || '',
    identitySource,
    identityConflict: Boolean(!explicitOwnerSessionId && localEvidence?.conflicted)
  }
}

/** 从前端本地 DAG Planning 登记构造 owner 候选，使 AG-UI 首帧前也能保护其它会话。 */
function localExecutionCandidate(
  entry: SessionExecutionEntry,
  sessions: readonly ApplicationOwnerSession[]
): OwnershipCandidate {
  const session = sessionForOwner(sessions, entry.identity.sessionId)
  // 本地登记必须和 lifecycle 使用同一个真实 Workflow thread；缺失时保持 undefined，
  // 让 resolver 进入 fail-closed conflicted，而不是把可见会话 thread 当成执行身份。
  const executionThreadId = String(entry.executionThreadId || '').trim() || undefined
  return {
    sessionId: entry.identity.sessionId,
    threadId: executionThreadId,
    title: session?.title,
    workbenchPhase: entry.identity.workbenchPhase,
    status: entry.status,
    source: 'active_dag_execution',
    updatedAt: '',
    identitySource: 'local_execution',
    identityConflict: false
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
    const preferredIdentity =
      OWNERSHIP_IDENTITY_PRIORITY[candidate.identitySource] >
      OWNERSHIP_IDENTITY_PRIORITY[previous.identitySource]
        ? candidate
        : previous
    const topPriority = OWNERSHIP_IDENTITY_PRIORITY[preferredIdentity.identitySource]
    const topIdentitySessionIds = new Set(
      [previous, candidate]
        .filter((item) => OWNERSHIP_IDENTITY_PRIORITY[item.identitySource] === topPriority)
        .map((item) => item.sessionId)
        .filter((sessionId): sessionId is string => Boolean(sessionId))
    )
    merged.set(key, {
      ...preferred,
      sessionId:
        preferredIdentity.sessionId ||
        preferred.sessionId ||
        previous.sessionId ||
        candidate.sessionId,
      threadId: preferred.threadId || previous.threadId || candidate.threadId,
      title: preferredIdentity.title || preferred.title || previous.title || candidate.title,
      workbenchPhase:
        preferredIdentity.workbenchPhase ||
        preferred.workbenchPhase ||
        previous.workbenchPhase ||
        candidate.workbenchPhase,
      identitySource: preferredIdentity.identitySource,
      identityConflict:
        previous.identityConflict || candidate.identityConflict || topIdentitySessionIds.size > 1
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

  const localIdentityEvidence = buildLocalExecutionIdentityEvidence(
    lifecycle,
    localExecutions,
    scope
  )
  const candidates: OwnershipCandidate[] = []
  Object.values(lifecycle?.activeExecutions || {}).forEach((execution) => {
    // 只有 prepare_build_tasks 的 running/stopping 代表 DAG generation 或 Regenerate。
    // awaiting_user 必须由上面的 actionable PendingPlan 单独确认，不能靠 execution 猜测。
    if (
      isDagPlanningPhase(execution.phase) &&
      ACTIVE_DAG_EXECUTION_STATUSES.has(execution.status)
    ) {
      candidates.push(executionCandidate(execution, sessions, localIdentityEvidence))
    }
  })
  localExecutions.forEach((entry) => {
    if (
      localExecutionBelongsToScope(entry, lifecycle, scope) &&
      isDagPlanningPhase(entry.phase) &&
      ACTIVE_LOCAL_EXECUTION_STATUSES.has(entry.status)
    ) {
      candidates.push(localExecutionCandidate(entry, sessions))
    }
  })

  const owners = mergeOwnershipCandidates(candidates)
  if (owners.length === 0) return { state: 'free', actionablePending: false }
  // DAG 活动状态出现多个无法归并的 thread 时 fail closed，避免任意一个会话误获写权限。
  if (
    owners.some((candidate) => !candidate.threadId || candidate.identityConflict) ||
    owners.length > 1
  ) {
    return { state: 'conflicted', actionablePending: false }
  }
  const owner = owners[0]
  const { updatedAt, identitySource, identityConflict, ...publicOwner } = owner
  void updatedAt
  void identitySource
  void identityConflict
  return { state: 'owned', owner: publicOwner, actionablePending: false }
}

/** 判断当前会话是否因另一 owner 或无法归并的活动执行而进入 Application readonly。 */
export function applicationMutationReadonlyForSession(
  ownership: ApplicationMutationOwnership,
  identity?: Pick<SessionIdentity, 'sessionId' | 'threadId'>
): boolean {
  if (ownership.state === 'conflicted') return true
  if (ownership.state !== 'owned' || !ownership.owner) return false
  // 一旦有稳定 sessionId，execution thread 只能作为诊断/展示信息，不能放宽其它会话的写权限。
  if (ownership.owner.sessionId) {
    return identity?.sessionId !== ownership.owner.sessionId
  }
  const sameThread = Boolean(
    identity && ownership.owner.threadId && identity.threadId === ownership.owner.threadId
  )
  // 仅在旧 lifecycle 缺少 session identity 且没有更高优先级证据时保留 thread fallback。
  return !sameThread
}

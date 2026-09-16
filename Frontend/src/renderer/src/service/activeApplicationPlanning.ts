import type {
  ApplicationConfig,
  ApplicationLifecycle,
  PlanningRefreshState,
  WorkflowRunPayload
} from '../typings'
import { retainApplicationPlanningInterrupt } from './applicationPlanningWorkflowState'
import { isApplicationCreationComplete, loadStoredApplications } from './applicationStorage'
import { getApplicationLifecycle } from './applicationLifecycle'
import type { ApplicationPlanningRecoveryProjection } from './applicationPlanningRecovery'
import {
  beginConnectionRequest,
  completeConnectionRequest,
  connectionAllowsMutation,
  failConnectionRequest,
  initialConnectionState,
  type ConnectionState
} from './connectionState'

export type ActivePlanningStatus = 'error' | 'ready' | 'running'
export type PlanningTransportState = 'idle' | 'running' | 'reconciling'

export type ApplicationPlanningCurrentState = {
  application: ApplicationConfig
  lifecycle: ApplicationLifecycle
  threadId: string
  transportState: PlanningTransportState
  /** Renderer 到 Backend 的通信状态；不包含任何 Workflow Recovery 事实。 */
  connection: ConnectionState
  /** 当前 renderer 是否由持久化状态恢复该规划，用于只执行一次本地产物冷恢复。 */
  restoreArtifactsFromDisk?: boolean
  /** 当前规划会话最近一次模型/Workflow 错误，仅用于前端实时展示。 */
  error?: string
  workflow?: WorkflowRunPayload
  /** Backend 根据 Durable Execution、checkpoint 与 Native Interrupt 生成的唯一恢复解释。 */
  recovery?: ApplicationPlanningRecoveryProjection
}

export type ApplicationPlanningCurrentEvent =
  | { type: 'run_started'; applicationId: string; threadId: string }
  | { type: 'run_settled'; applicationId: string; threadId: string }
  | {
      type: 'workflow_received'
      applicationId: string
      threadId: string
      workflow: WorkflowRunPayload
    }
  | {
      type: 'lifecycle_received'
      applicationId: string
      threadId: string
      lifecycle: ApplicationLifecycle
    }
  | {
      type: 'run_failed'
      applicationId: string
      threadId: string
      error: string
      workflow?: WorkflowRunPayload
    }
  | {
      type: 'reconcile_started'
      applicationId: string
      threadId: string
      requestGeneration: number
    }
  | {
      type: 'reconcile_received'
      applicationId: string
      threadId: string
      lifecycle: ApplicationLifecycle
      workflow: WorkflowRunPayload
      recovery: ApplicationPlanningRecoveryProjection
      requestGeneration: number
    }
  | {
      type: 'reconcile_failed'
      applicationId: string
      threadId: string
      error: string
      requestGeneration: number
    }
  | { type: 'clear_error'; applicationId: string; threadId: string }
  | {
      type: 'application_received'
      applicationId: string
      threadId: string
      application: ApplicationConfig
    }

/** 从 Pending interaction 读取可比较的服务端草稿身份。 */
function pendingDraftIdentity(value: unknown): string | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const identity = value as Record<string, unknown>
  const planningRunId = String(identity.planningRunId || '').trim()
  const draftDigest = String(identity.draftDigest || '').trim()
  return planningRunId && /^[0-9a-f]{64}$/.test(draftDigest)
    ? `${planningRunId}:${draftDigest}`
    : undefined
}

/** 判断 execution 是否为与恢复投影一致、尚未提交的 Build DAG 待确认交互。 */
function isPendingDagExecution(
  lifecycle: ApplicationLifecycle,
  refresh?: PlanningRefreshState,
  requireDraftIdentity = false
): boolean {
  const refreshIdentity = refresh
    ? pendingDraftIdentity({
        planningRunId: refresh.planningRunId,
        draftDigest: refresh.draftDigest
      })
    : undefined
  if (requireDraftIdentity && !refreshIdentity) return false
  return Object.values(lifecycle.activeExecutions || {}).some(
    (execution) =>
      (!refresh?.workflowRunId || execution.runId === refresh.workflowRunId) &&
      (!refreshIdentity ||
        pendingDraftIdentity(execution.pendingInteraction?.payload?.draftIdentity) ===
          refreshIdentity) &&
      execution.status === 'awaiting_user' &&
      !execution.pendingInteraction?.submittedAt &&
      (execution.pendingInteraction?.type === 'task_plan_confirmation' ||
        execution.pendingInteraction?.payload?.mode === 'build_task_plan_confirmation')
  )
}

/** 判断 refresh 是否已被 lifecycle 中较新的 Pending execution 越过。 */
function planningRefreshConflictsWithLifecycle(
  lifecycle: ApplicationLifecycle,
  refresh: NonNullable<ApplicationLifecycle['extensions']['planningRefresh']>
): boolean {
  return (
    refresh.source === 'active_planning_run' &&
    refresh.status === 'planning' &&
    isPendingDagExecution(lifecycle)
  )
}

/** 在持久 revision 之外合并 GET-time refresh，同时拒绝与当前 execution 冲突的旧帧。 */
function mergePlanningRefresh(
  base: ApplicationLifecycle,
  current: ApplicationLifecycle,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  const incomingRefresh = incoming.extensions?.planningRefresh
  const currentRefresh = current.extensions?.planningRefresh
  let planningRefresh = incomingRefresh

  if (incoming.revision < current.revision) {
    // 低 revision 的恢复读取只在能被当前 Pending execution 直接印证时采用。
    planningRefresh =
      incomingRefresh?.source === 'pending_plan' &&
      incomingRefresh.status === 'awaiting_confirmation' &&
      isPendingDagExecution(base, incomingRefresh, true)
        ? incomingRefresh
        : currentRefresh
  } else if (!incomingRefresh && incoming.revision === current.revision) {
    // 同 revision 且没有新的恢复读取时，只合并持久字段，不主动清除现有 GET 投影。
    planningRefresh = currentRefresh
  } else if (!incomingRefresh) {
    // 缺少 projection 只表示本次 lifecycle 帧没有提供它，不能把已有权威 GET 结果清掉。
    planningRefresh = currentRefresh
  }

  // 无论 refresh 来自当前帧还是沿用了旧投影，都不能让 active_planning_run
  // 越过同一 lifecycle 中已经进入 awaiting_confirmation 的 Pending execution。
  if (
    planningRefresh &&
    (planningRefreshConflictsWithLifecycle(base, planningRefresh) ||
      planningRefreshConflictsWithLifecycle(current, planningRefresh))
  ) {
    planningRefresh = undefined
  }
  if (planningRefresh === base.extensions?.planningRefresh) return base
  const extensions = { ...base.extensions }
  if (planningRefresh) extensions.planningRefresh = planningRefresh
  else delete extensions.planningRefresh
  return { ...base, extensions }
}

/** 按持久 lifecycle revision 合并 GET-time recovery projection，避免旧帧覆盖新状态。 */
function mergeExecutionRecovery(
  base: ApplicationLifecycle,
  current: ApplicationLifecycle,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  const currentProjection = current.extensions?.executionRecovery
  const incomingProjection = incoming.extensions?.executionRecovery
  let projection: typeof currentProjection

  if (incoming.revision < current.revision) {
    // 旧持久 revision 的 GET 不能覆盖更新状态。
    projection = currentProjection
  } else if (incomingProjection) {
    // 同 revision / 新 revision 的 GET projection 都是最新 runtime authority。
    projection = incomingProjection
  } else {
    // 普通 Workflow lifecycle 帧没有 GET-time projection，不能因此清掉已有 projection。
    projection = currentProjection
  }

  const extensions = { ...base.extensions }
  if (projection) extensions.executionRecovery = projection
  else delete extensions.executionRecovery
  return {
    ...base,
    extensions
  }
}

/**
 * 按应用标识和单调 revision 合并持久化 lifecycle。
 * planningRefresh 是 Backend GET 时临时计算的恢复投影，不参与持久化 revision；
 * 因此同 revision 的重新校准允许只替换该 extension，不能让旧持久化字段倒退。
 */
export function latestApplicationLifecycle(
  current: ApplicationLifecycle | undefined,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  if (!current || current.application.id !== incoming.application.id) return incoming
  const base = incoming.revision > current.revision ? incoming : current
  return mergeExecutionRecovery(
    mergePlanningRefresh(base, current, incoming),
    current,
    incoming
  )
}

// 直接根据权威 lifecycle 状态计算首页展示状态。
export function activePlanningStatus(lifecycle: ApplicationLifecycle): ActivePlanningStatus {
  if (lifecycle.initialization.status === 'failed') return 'error'
  if (
    lifecycle.initialization.status === 'awaiting_user' ||
    lifecycle.initialization.status === 'cancelled' ||
    lifecycle.initialization.status === 'stopped'
  )
    return 'ready'
  return 'running'
}

/** 从唯一当前状态派生首页与错误卡片所需的展示状态。 */
export function applicationPlanningDisplayStatus(
  state: ApplicationPlanningCurrentState
): ActivePlanningStatus {
  if (planningTransportBusy(state)) return 'running'
  if (state.error) return 'error'
  return activePlanningStatus(state.lifecycle)
}

/** 判断当前 transport 是否正在执行 Graph 写入或权威状态同步。 */
export function planningTransportBusy(
  state?: ApplicationPlanningCurrentState
): boolean {
  return state?.transportState === 'running' || state?.transportState === 'reconciling'
}

/** 判断 mutation 是否必须等待 transport 回到已确认的 idle 状态。 */
export function planningMutationBlocked(
  state?: ApplicationPlanningCurrentState
): boolean {
  return Boolean(
    state &&
      (state.transportState !== 'idle' || !connectionAllowsMutation(state.connection))
  )
}

/** 从恢复投影、Workflow、lifecycle 与当前运行态中恢复真实失败原因，绝不使用恢复说明覆盖错误。 */
function recoverablePlanningFailureMessage(
  current: ApplicationPlanningCurrentState,
  workflow: WorkflowRunPayload,
  lifecycle: ApplicationLifecycle,
  recovery: ApplicationPlanningRecoveryProjection
): string {
  const diagnosticMessage = recovery.failureDiagnostic?.message?.trim()
  if (diagnosticMessage) return diagnosticMessage

  const workflowFailure =
    workflow.summary.status === 'failed' ? workflow.summary.message?.trim() : ''
  if (workflowFailure) return workflowFailure

  const lifecycleFailure =
    lifecycle.initialization.status === 'failed' ? lifecycle.error?.message?.trim() : ''
  if (lifecycleFailure) return lifecycleFailure

  const currentError = current.error?.trim()
  if (currentError && currentError !== recovery.message.trim()) return currentError

  if (recovery.failureDiagnostic?.httpStatus) {
    return `HTTP ${recovery.failureDiagnostic.httpStatus}`
  }

  if (recovery.failureDiagnostic?.code) return recovery.failureDiagnostic.code

  return '上一次计划执行失败。'
}

/** 合并 Workflow 及其 lifecycle，并保留同一运行中的原生中断投影。 */
function reducePlanningWorkflow(
  current: ApplicationPlanningCurrentState,
  workflow: WorkflowRunPayload
): ApplicationPlanningCurrentState {
  if (workflow.threadId !== current.threadId) return current
  const mergedWorkflow = retainApplicationPlanningInterrupt(current.workflow, workflow)
  const workflowLifecycle = workflowApplicationLifecycle(mergedWorkflow)
  const lifecycle = workflowLifecycle
    ? latestApplicationLifecycle(current.lifecycle, workflowLifecycle)
    : current.lifecycle
  if (mergedWorkflow === current.workflow && lifecycle === current.lifecycle) return current
  return {
    ...current,
    connection: completeConnectionRequest(
      current.connection,
      current.connection.requestGeneration
    ),
    lifecycle,
    workflow: mergedWorkflow
  }
}

/** 通过单一事件入口更新某个 application planning thread 的当前业务状态。 */
export function reduceApplicationPlanningCurrentState(
  current: ApplicationPlanningCurrentState,
  event: ApplicationPlanningCurrentEvent
): ApplicationPlanningCurrentState {
  if (event.applicationId !== current.application.id || event.threadId !== current.threadId) {
    return current
  }

  if (event.type === 'run_started') {
    return {
      ...current,
      error: undefined,
      transportState: 'running'
    }
  }
  if (event.type === 'run_settled') {
    return { ...current, transportState: 'idle' }
  }
  if (event.type === 'clear_error') {
    return current.error ? { ...current, error: undefined } : current
  }
  if (event.type === 'reconcile_started') {
    return {
      ...current,
      connection: beginConnectionRequest(current.connection, event.requestGeneration),
      transportState: 'reconciling'
    }
  }
  if (event.type === 'reconcile_failed') {
    if (event.requestGeneration < current.connection.requestGeneration) return current
    return {
      ...current,
      connection: failConnectionRequest(
        current.connection,
        event.requestGeneration,
        event.error
      ),
      transportState: 'idle'
    }
  }
  if (event.type === 'reconcile_received') {
    if (event.requestGeneration < current.connection.requestGeneration) return current
    if (
      event.lifecycle.application.id !== current.application.id ||
      event.workflow.threadId !== current.threadId
    ) {
      return current
    }
    // reconcile 是完整权威快照，必须覆盖本地为流式增量保留的旧 interrupt。
    const workflowLifecycle = workflowApplicationLifecycle(event.workflow)
    const lifecycle = latestApplicationLifecycle(
      current.lifecycle,
      workflowLifecycle
        ? latestApplicationLifecycle(workflowLifecycle, event.lifecycle)
        : event.lifecycle
    )
    let error = current.error
    if (event.recovery.classification === 'ready_to_continue') {
      error = recoverablePlanningFailureMessage(
        current,
        event.workflow,
        lifecycle,
        event.recovery
      )
    } else if (
      ['failed', 'blocked', 'conflict', 'legacy_unverified'].includes(
        event.recovery.classification
      )
    ) {
      error = event.recovery.message
    } else if (event.workflow.summary.status === 'failed') {
      error = event.workflow.summary.message || current.error || '规划运行失败。'
    } else if (lifecycle.initialization.status === 'failed') {
      error = lifecycle.error?.message || current.error || '规划运行失败。'
    } else {
      error = undefined
    }
    return {
      ...current,
      workflow: event.workflow,
      lifecycle,
      recovery: event.recovery,
      error,
      connection: completeConnectionRequest(current.connection, event.requestGeneration),
      transportState: 'idle'
    }
  }
  if (event.type === 'application_received') {
    return event.application.id === current.application.id
      ? { ...current, application: event.application }
      : current
  }
  if (event.type === 'lifecycle_received') {
    if (event.lifecycle.application.id !== current.application.id) return current
    const lifecycle = latestApplicationLifecycle(current.lifecycle, event.lifecycle)
    return {
      ...current,
      connection: completeConnectionRequest(
        current.connection,
        current.connection.requestGeneration
      ),
      lifecycle
    }
  }
  if (event.type === 'workflow_received') {
    return reducePlanningWorkflow(current, event.workflow)
  }

  const next = event.workflow ? reducePlanningWorkflow(current, event.workflow) : current
  const error = event.error.trim() || '规划运行失败。'
  return {
    ...next,
    connection: completeConnectionRequest(
      next.connection,
      next.connection.requestGeneration
    ),
    error,
    transportState: 'idle'
  }
}

// 从应用目录逐一读取生命周期，并返回全部未完成创建流程。
export async function loadActiveApplicationPlannings(): Promise<ApplicationPlanningCurrentState[]> {
  const recoveredActive: ApplicationPlanningCurrentState[] = []
  const applications = (await loadStoredApplications())
    .filter((application) => application.source === 'new' && application.workspaceRoot)
    .sort((left, right) => right.createdAt - left.createdAt)

  for (const application of applications) {
    try {
      const lifecycle = await getApplicationLifecycle(application)
      if (isApplicationCreationComplete(lifecycle)) continue
      const threadId = lifecycle.initialization.threadId
      if (!threadId) {
        throw new Error(`应用 ${application.id} 缺少初始化线程标识。`)
      }
      recoveredActive.push({
        application,
        lifecycle,
        restoreArtifactsFromDisk: true,
        threadId,
        connection: initialConnectionState(true),
        transportState: 'idle'
      })
    } catch (error) {
      // 历史/已删除工作区的 application-lifecycle.json 不存在属正常情况，
      // 静默跳过即可，避免每次刷新都刷屏。
      const message = error instanceof Error ? error.message : String(error)
      if (!message.includes('application-lifecycle.json 不存在')) {
        console.warn('读取应用生命周期失败', error)
      }
    }
  }
  return recoveredActive
}

// 从 AG-UI Workflow 快照读取后端直接投影的 lifecycle。
export function workflowApplicationLifecycle(
  workflow?: WorkflowRunPayload
): ApplicationLifecycle | undefined {
  for (const source of [workflow?.result, workflow?.state]) {
    const lifecycle = source?.lifecycle
    if (lifecycle && typeof lifecycle === 'object') {
      return lifecycle as ApplicationLifecycle
    }
  }
  return undefined
}

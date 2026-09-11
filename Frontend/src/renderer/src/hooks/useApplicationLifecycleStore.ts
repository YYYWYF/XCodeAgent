import { useCallback, useEffect, useState } from 'react'
import type { ApplicationLifecycle, PlanningRefreshState } from '../typings'

const NON_TERMINAL_EXECUTION_STATUSES = new Set(['running', 'stopping', 'awaiting_user'])

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
  refresh?: PlanningRefreshState
): boolean {
  const refreshIdentity = refresh
    ? pendingDraftIdentity({
        planningRunId: refresh.planningRunId,
        draftDigest: refresh.draftDigest
      })
    : undefined
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

/** 判断生成中 refresh 是否已被同一 lifecycle 中较新的 Pending execution 越过。 */
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
      isPendingDagExecution(base, incomingRefresh)
        ? incomingRefresh
        : currentRefresh
  } else if (!incomingRefresh && incoming.revision === current.revision) {
    // 同 revision 且没有新的恢复读取时，只合并持久字段，不主动清除现有 GET 投影。
    planningRefresh = currentRefresh
  } else if (!incomingRefresh) {
    // 缺少 projection 只表示本次 lifecycle 帧没有提供它，不能把已有权威 GET 结果清掉。
    planningRefresh = currentRefresh
  }

  if (
    incomingRefresh &&
    planningRefresh &&
    planningRefreshConflictsWithLifecycle(base, planningRefresh)
  ) {
    planningRefresh = undefined
  }
  if (planningRefresh === base.extensions?.planningRefresh) return base
  const extensions = { ...base.extensions }
  if (planningRefresh) extensions.planningRefresh = planningRefresh
  else delete extensions.planningRefresh
  return { ...base, extensions }
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
  return mergePlanningRefresh(base, current, incoming)
}
/** 判断应用是否仍有需要在后台继续持有的非终态执行。 */
export function hasNonTerminalApplicationExecution(lifecycle?: ApplicationLifecycle): boolean {
  return Object.values(lifecycle?.activeExecutions || {}).some((execution) =>
    NON_TERMINAL_EXECUTION_STATUSES.has(execution.status)
  )
}

/** 为整个工作台提供单一 application lifecycle store，统一接收恢复读取与 AG-UI 事件。 */
export function useApplicationLifecycleStore(applicationId: string): {
  lifecycle?: ApplicationLifecycle
  mergeLifecycle: (lifecycle: ApplicationLifecycle) => void
} {
  const [lifecycle, setLifecycle] = useState<ApplicationLifecycle>()
  const applicationLifecycle = lifecycle?.application.id === applicationId ? lifecycle : undefined

  // 切换应用时立即丢弃上一应用的快照，避免校准请求返回前短暂串用资源锁。
  useEffect(() => {
    setLifecycle((current) => (current?.application.id === applicationId ? current : undefined))
  }, [applicationId])

  // 实时事件、冷启动读取和重连校准共享持久化 revision；GET-time planningRefresh 单独刷新。
  const mergeLifecycle = useCallback((incoming: ApplicationLifecycle): void => {
    setLifecycle((current) => latestApplicationLifecycle(current, incoming))
  }, [])

  return { lifecycle: applicationLifecycle, mergeLifecycle }
}

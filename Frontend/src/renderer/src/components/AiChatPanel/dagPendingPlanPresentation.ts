import type { DagGenerationSnapshot } from '../../service/agUiAgent'

export type DagGenerationCompletionSummary = {
  readyUnitCount: number
  unitCount: number
  addedTaskCount: number
  reusedTaskCount: number
}

export type DagPendingPlanCompletion = {
  planningRunId?: string
  summary?: DagGenerationCompletionSummary
}

export type PendingDagGuidanceStorage = Pick<Storage, 'getItem' | 'setItem'>

/** 仅当当前会话是服务端确认的 PendingPlan owner 时渲染右侧确认入口。 */
export function shouldRenderPendingDagConfirmationTab(hasOwnedPendingPlan: boolean): boolean {
  return hasOwnedPendingPlan
}

/** 安全读取应用内的轻量 UI 引导存储；不可用时由当前组件实例维持一次性状态。 */
export function browserPendingDagGuidanceStorage(): PendingDagGuidanceStorage | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

/** 从后端签发的 DAG 身份构造用于比较的键；身份不完整时 fail closed。 */
export function pendingDagPlanIdentityKey(
  identity: { planningRunId: string; draftDigest: string } | undefined
): string | undefined {
  const planningRunId = String(identity?.planningRunId || '').trim()
  const draftDigest = String(identity?.draftDigest || '').trim()
  return planningRunId && /^[0-9a-f]{64}$/.test(draftDigest)
    ? `${planningRunId}:${draftDigest}`
    : undefined
}

/** 为应用和 Pending owner 生成隔离的自动引导记录键。 */
export function pendingDagGuidanceStorageKey(
  applicationId: string,
  ownerSessionId: string
): string | undefined {
  const appId = applicationId.trim()
  const sessionId = ownerSessionId.trim()
  return appId && sessionId
    ? `devagentstudio:dag-pending-guided:${encodeURIComponent(appId)}:${encodeURIComponent(sessionId)}`
    : undefined
}

/** 判断同一 DraftIdentity 是否已经自动引导，存储不可用时由调用方内存状态兜底。 */
export function wasPendingDagPlanAutoGuided(
  storage: PendingDagGuidanceStorage | undefined,
  storageKey: string | undefined,
  identityKey: string | undefined
): boolean {
  if (!storage || !storageKey || !identityKey) return false
  try {
    return storage.getItem(storageKey) === identityKey
  } catch {
    return false
  }
}

/** 记录当前 DraftIdentity 已完成自动引导，不把这条 UI 记录当作 PendingPlan 权威。 */
export function recordPendingDagPlanAutoGuided(
  storage: PendingDagGuidanceStorage | undefined,
  storageKey: string | undefined,
  identityKey: string | undefined
): boolean {
  if (!storage || !storageKey || !identityKey) return false
  try {
    storage.setItem(storageKey, identityKey)
    return true
  } catch {
    return false
  }
}

/** 仅当 DraftIdentity 与上一次自动引导记录不同时触发首次导航。 */
export function shouldAutoGuidePendingDagPlan(
  identityKey: string | undefined,
  lastGuidedIdentityKey: string
): boolean {
  return Boolean(identityKey && identityKey !== lastGuidedIdentityKey)
}

/** 手动导航不受自动引导去重影响；非 owner 禁止进入，同视图则保留阅读位置。 */
export function pendingDagManualNavigationDecision(
  hasOwnedPendingPlan: boolean,
  alreadyShowingConfirmation: boolean
): 'unavailable' | 'already-showing' | 'navigate' {
  if (!hasOwnedPendingPlan) return 'unavailable'
  return alreadyShowingConfirmation ? 'already-showing' : 'navigate'
}

/** 只为同一 PlanningRun 的最终待确认快照生成可信摘要，拒绝中间态和不完整计数。 */
export function dagGenerationCompletionSummary(
  snapshot: DagGenerationSnapshot | undefined,
  planningRunId: string | undefined
): DagGenerationCompletionSummary | undefined {
  if (
    !snapshot ||
    !planningRunId ||
    snapshot.planningRunId !== planningRunId ||
    snapshot.status !== 'active' ||
    snapshot.phase !== 'persisting_pending' ||
    snapshot.summary.unitCount <= 0 ||
    snapshot.summary.readyUnitCount !== snapshot.summary.unitCount ||
    snapshot.summary.activeUnitCount !== 0 ||
    snapshot.summary.pendingUnitCount !== 0 ||
    snapshot.summary.roundExhaustedUnitCount !== 0 ||
    snapshot.summary.abortedUnitCount !== 0 ||
    snapshot.summary.failureIssueCount !== 0 ||
    snapshot.summary.globalIssueCount !== 0 ||
    snapshot.globalIssues.length > 0 ||
    snapshot.units.length !== snapshot.summary.unitCount ||
    snapshot.units.some(
      (unit) => unit.status !== 'candidate_ready' && unit.status !== 'not_required'
    )
  ) {
    return undefined
  }

  const counts = [
    snapshot.summary.readyUnitCount,
    snapshot.summary.unitCount,
    snapshot.summary.candidateTaskCount,
    snapshot.summary.retainedTaskCount
  ]
  if (counts.some((count) => !Number.isSafeInteger(count) || count < 0)) return undefined

  return {
    readyUnitCount: snapshot.summary.readyUnitCount,
    unitCount: snapshot.summary.unitCount,
    addedTaskCount: snapshot.summary.candidateTaskCount,
    reusedTaskCount: snapshot.summary.retainedTaskCount
  }
}

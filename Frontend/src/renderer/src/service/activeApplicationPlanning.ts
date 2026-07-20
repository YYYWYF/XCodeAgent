import type { ApplicationConfig, WorkflowRunPayload } from '../typings'
import { loadStoredApplications } from './applicationStorage'
import { createPagePlanningThreadId } from './applicationPagePlanning'

const ACTIVE_PLANNING_STORAGE_KEY = 'xcode-agent-active-application-planning-v1'
const COMPLETED_PLANNING_THREADS_STORAGE_KEY = 'xcode-agent-completed-application-planning-threads-v1'

export type ActivePlanningStatus = 'error' | 'ready' | 'running'

export type PersistedActivePlanning = {
  application: ApplicationConfig
  status: ActivePlanningStatus
  threadId: string
  workflow?: WorkflowRunPayload
}

// 判断本地缓存值是否包含恢复规划所需的最小字段。
function isPersistedActivePlanning(value: unknown): value is PersistedActivePlanning {
  if (!value || typeof value !== 'object') return false
  const planning = value as Partial<PersistedActivePlanning>
  return Boolean(
    planning.application &&
      typeof planning.application === 'object' &&
      typeof planning.threadId === 'string' &&
      planning.threadId.trim() &&
      ['error', 'ready', 'running'].includes(String(planning.status))
  )
}

// 读取已经完成的规划线程，阻止恢复逻辑再次创建首页入口。
function loadCompletedPlanningThreads(): Set<string> {
  try {
    const value = JSON.parse(
      window.localStorage.getItem(COMPLETED_PLANNING_THREADS_STORAGE_KEY) || '[]'
    ) as unknown
    return new Set(
      Array.isArray(value)
        ? value.filter((item): item is string => typeof item === 'string')
        : []
    )
  } catch {
    return new Set()
  }
}

// 判断 Workflow 是否已经返回最终的规划确认结果。
function hasPlanningConfirmation(workflow?: WorkflowRunPayload): boolean {
  return [workflow?.result, workflow?.state].some((source) => {
    const confirmation = source?.application_planning_confirmation
    return Boolean(confirmation && typeof confirmation === 'object')
  })
}

// 判断活动规划是否已经完成最终确认，不再属于首页可恢复任务。
export function isApplicationPlanningConfirmed(planning: PersistedActivePlanning): boolean {
  return Boolean(planning.application.planningConfirmedAt) || hasPlanningConfirmation(planning.workflow)
}

// 记录已经完成的线程，兼容修复前残留的活动规划缓存。
function markPlanningThreadCompleted(threadId: string): void {
  const completedThreads = loadCompletedPlanningThreads()
  completedThreads.add(threadId)
  window.localStorage.setItem(
    COMPLETED_PLANNING_THREADS_STORAGE_KEY,
    JSON.stringify(Array.from(completedThreads))
  )
}

// 从 localStorage 恢复尚未完成的应用规划会话。
export function loadActiveApplicationPlanning(): PersistedActivePlanning | undefined {
  try {
    const rawValue = window.localStorage.getItem(ACTIVE_PLANNING_STORAGE_KEY)
    if (!rawValue) return undefined
    const planning = JSON.parse(rawValue) as unknown
    if (!isPersistedActivePlanning(planning)) return undefined
    if (isApplicationPlanningConfirmed(planning)) {
      clearActiveApplicationPlanning(planning.threadId)
      return undefined
    }
    return planning
  } catch {
    return undefined
  }
}

// 持久化规划线程、首页状态和最新 Workflow 快照。
export function saveActiveApplicationPlanning(planning: PersistedActivePlanning): void {
  window.localStorage.setItem(ACTIVE_PLANNING_STORAGE_KEY, JSON.stringify(planning))
}

// 清除已经确认完成的规划会话，并按线程阻止后续恢复。
export function clearActiveApplicationPlanning(threadId?: string): void {
  if (threadId) markPlanningThreadCompleted(threadId)
  window.localStorage.removeItem(ACTIVE_PLANNING_STORAGE_KEY)
}

// 校验活动规划是否仍对应应用索引中的同一条未完成记录。
export async function isActiveApplicationPlanningIndexed(
  planning: PersistedActivePlanning
): Promise<boolean> {
  const applications = await loadStoredApplications()
  return applications.some(
    (application) =>
      application.id === planning.application.id &&
      !application.planningConfirmedAt &&
      (!application.planningThreadId || application.planningThreadId === planning.threadId)
  )
}

// 从应用索引找回丢失的规划记录；兼容本次修复前创建但尚未完成的最近应用。
export async function recoverActiveApplicationPlanning(): Promise<
  PersistedActivePlanning | undefined
> {
  const completedThreads = loadCompletedPlanningThreads()
  const applications = (await loadStoredApplications())
    .filter(
      (application) =>
        application.source === 'new' &&
        application.workspaceRoot &&
        !application.planningConfirmedAt &&
        (!application.planningThreadId || !completedThreads.has(application.planningThreadId))
    )
    .sort((left, right) => right.createdAt - left.createdAt)
  const indexedApplication = applications.find((application) => application.planningThreadId)
  const recentCutoff = Date.now() - 24 * 60 * 60 * 1000
  const legacyApplication = applications.find((application) => application.createdAt >= recentCutoff)
  const application = indexedApplication || legacyApplication
  if (!application) return undefined

  const threadId = application.planningThreadId || createPagePlanningThreadId()
  return {
    application: { ...application, planningThreadId: threadId },
    status: 'running',
    threadId
  }
}

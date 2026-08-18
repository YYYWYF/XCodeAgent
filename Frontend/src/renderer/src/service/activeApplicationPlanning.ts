import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { canOpenApplicationWorkbench, loadStoredApplications } from './applicationStorage'
import { getApplicationLifecycle } from './applicationLifecycle'

export type ActivePlanningStatus = 'error' | 'ready' | 'running'

export type PersistedActivePlanning = {
  application: ApplicationConfig
  lifecycle: ApplicationLifecycle
  status: ActivePlanningStatus
  threadId: string
  workflow?: WorkflowRunPayload
}

export const MAX_ACTIVE_APPLICATION_PLANS = 3

// 直接根据权威 lifecycle 状态计算首页展示状态。
export function activePlanningStatus(lifecycle: ApplicationLifecycle): ActivePlanningStatus {
  if (lifecycle.initialization.status === 'failed') return 'error'
  if (
    lifecycle.initialization.status === 'awaiting_user' ||
    lifecycle.initialization.status === 'cancelled'
  )
    return 'ready'
  return 'running'
}

// 从应用目录逐一读取生命周期，并返回全部未完成创建流程。
export async function loadActiveApplicationPlannings(): Promise<PersistedActivePlanning[]> {
  const recoveredActive: PersistedActivePlanning[] = []
  const applications = (await loadStoredApplications())
    .filter((application) => application.source === 'new' && application.workspaceRoot)
    .sort((left, right) => right.createdAt - left.createdAt)

  for (const application of applications) {
    if (canOpenApplicationWorkbench(application)) continue
    try {
      const lifecycle = await getApplicationLifecycle(application)
      if (canOpenApplicationWorkbench(application, lifecycle)) continue
      const threadId = lifecycle.initialization.threadId
      if (!threadId) {
        throw new Error(`应用 ${application.id} 缺少初始化线程标识。`)
      }
      recoveredActive.push({
        application,
        lifecycle,
        status: activePlanningStatus(lifecycle),
        threadId
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

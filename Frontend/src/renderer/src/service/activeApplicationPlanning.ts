import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import {
  canOpenApplicationWorkbench,
  loadStoredApplications
} from './applicationStorage'
import { getApplicationLifecycle } from './applicationPagePlanning'

export type ActivePlanningStatus = 'error' | 'ready' | 'running'

export type PersistedActivePlanning = {
  application: ApplicationConfig
  lifecycle: ApplicationLifecycle
  status: ActivePlanningStatus
  threadId: string
  workflow?: WorkflowRunPayload
}

// 直接根据权威 lifecycle 状态计算首页展示状态。
export function activePlanningStatus(lifecycle: ApplicationLifecycle): ActivePlanningStatus {
  if (lifecycle.initialization.status === 'failed') return 'error'
  if (
    lifecycle.initialization.status === 'awaiting_user' ||
    lifecycle.initialization.status === 'cancelled'
  ) return 'ready'
  return 'running'
}

// 从应用目录逐一读取生命周期，并返回最近的未完成创建流程。
export async function loadActiveApplicationPlanning(): Promise<
  PersistedActivePlanning | undefined
> {
  let recoveredActive: PersistedActivePlanning | undefined
  const applications = (await loadStoredApplications())
    .filter((application) => application.source === 'new' && application.workspaceRoot)
    .sort((left, right) => right.createdAt - left.createdAt)

  for (const application of applications) {
    if (canOpenApplicationWorkbench(application)) continue
    try {
      const lifecycle = await getApplicationLifecycle(application)
      if (canOpenApplicationWorkbench(application, lifecycle)) continue
      if (!recoveredActive) {
        const threadId = lifecycle.initialization.threadId
        if (!threadId) {
          throw new Error(`应用 ${application.id} 缺少初始化线程标识。`)
        }
        recoveredActive = {
          application,
          lifecycle,
          status: activePlanningStatus(lifecycle),
          threadId
        }
      }
    } catch (error) {
      console.warn('读取应用生命周期失败', error)
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

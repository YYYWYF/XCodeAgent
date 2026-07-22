import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { loadStoredApplications } from './applicationStorage'
import { createPagePlanningThreadId, getApplicationLifecycle } from './applicationPagePlanning'

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
  if (lifecycle.lifecycle.status === 'failed') return 'error'
  if (
    lifecycle.lifecycle.status === 'awaiting_user' ||
    lifecycle.lifecycle.status === 'cancelled'
  ) return 'ready'
  return 'running'
}

// 判断生命周期是否已经生成应用模板文件并允许进入工作台。
export function isApplicationPlanningConfirmed(lifecycle: ApplicationLifecycle): boolean {
  return lifecycle.lifecycle.stage === 'ready_for_workbench'
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
    try {
      const lifecycle = await getApplicationLifecycle(application)
      if (isApplicationPlanningConfirmed(lifecycle)) continue
      if (!recoveredActive) {
        recoveredActive = {
          application,
          lifecycle,
          status: activePlanningStatus(lifecycle),
          threadId: lifecycle.activeThreadId || createPagePlanningThreadId()
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

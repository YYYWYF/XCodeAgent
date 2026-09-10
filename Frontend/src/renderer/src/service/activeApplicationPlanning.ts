import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { retainApplicationPlanningInterrupt } from '../components/Welcome/planningWorkflowState'
import { isApplicationCreationComplete, loadStoredApplications } from './applicationStorage'
import { getApplicationLifecycle } from './applicationLifecycle'

export type ActivePlanningStatus = 'error' | 'ready' | 'running'
export type PlanningTransportState = 'idle' | 'running'

export type ApplicationPlanningCurrentState = {
  application: ApplicationConfig
  lifecycle: ApplicationLifecycle
  threadId: string
  transportState: PlanningTransportState
  /** 当前 renderer 是否由持久化状态恢复该规划，用于只执行一次本地产物冷恢复。 */
  restoreArtifactsFromDisk?: boolean
  /** 当前规划会话最近一次模型/Workflow 错误，仅用于前端实时展示。 */
  error?: string
  workflow?: WorkflowRunPayload
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
  | { type: 'clear_error'; applicationId: string; threadId: string }
  | {
      type: 'application_received'
      applicationId: string
      threadId: string
      application: ApplicationConfig
    }

/** 按应用标识和单调 revision 合并 lifecycle，拒绝冷启动读取覆盖更新的实时投影。 */
export function latestApplicationLifecycle(
  current: ApplicationLifecycle | undefined,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  if (!current || current.application.id !== incoming.application.id) return incoming
  return incoming.revision > current.revision ? incoming : current
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
  if (state.transportState === 'running') return 'running'
  if (state.error) return 'error'
  return activePlanningStatus(state.lifecycle)
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
    return { ...current, error: undefined, transportState: 'running' }
  }
  if (event.type === 'run_settled') {
    return { ...current, transportState: 'idle' }
  }
  if (event.type === 'clear_error') {
    return current.error ? { ...current, error: undefined } : current
  }
  if (event.type === 'application_received') {
    return event.application.id === current.application.id
      ? { ...current, application: event.application }
      : current
  }
  if (event.type === 'lifecycle_received') {
    if (event.lifecycle.application.id !== current.application.id) return current
    const lifecycle = latestApplicationLifecycle(current.lifecycle, event.lifecycle)
    return lifecycle === current.lifecycle ? current : { ...current, lifecycle }
  }
  if (event.type === 'workflow_received') {
    return reducePlanningWorkflow(current, event.workflow)
  }

  const next = event.workflow ? reducePlanningWorkflow(current, event.workflow) : current
  const error = event.error.trim() || '规划运行失败。'
  return { ...next, error, transportState: 'idle' }
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

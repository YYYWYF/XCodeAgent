import { randomUUID, type AgentSubscriber } from '@ag-ui/client'
import type { WorkflowRunPayload } from '../typings'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'
import { workflowApplicationLifecycle, type ApplicationPlanningCurrentState } from './activeApplicationPlanning'

export type PlanningStageTarget = { threadId: string; phase: 'product' | 'planning' }
export type PlanningStageEntry = PlanningStageTarget | (() => Promise<PlanningStageTarget>)

/** 通过标准 AG-UI 将当前服务端产物与确认门交给独立阶段线程，失败时不推进业务。 */
export async function handoffPlanningStage(
  current: ApplicationPlanningCurrentState, target: PlanningStageTarget
): Promise<WorkflowRunPayload> {
  const sourceRunId = current.workflow?.runId
  if (!sourceRunId) throw new Error('当前规划缺少可交接的执行身份，请先同步状态。')
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: target.threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '交接阶段会话及当前产物确认状态。' })
  let result: Record<string, unknown> | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'planning-stage-handoff' && event.value && typeof event.value === 'object')
        result = event.value as Record<string, unknown>
    }
  }
  await agent.runAgent({ forwardedProps: { planningStageHandoff: {
    workspaceRoot: current.application.workspaceRoot, applicationId: current.application.id,
    sourceThreadId: current.threadId, sourceRunId, lifecycleRevision: current.lifecycle.revision,
    targetPhase: target.phase
  } } }, subscriber)
  if (result?.status === 'failed')
    throw new Error((result.error as { message?: string } | undefined)?.message || '阶段会话交接失败。')
  const workflow = result?.workflow as WorkflowRunPayload | undefined
  const lifecycle = workflowApplicationLifecycle(workflow)
  if (result?.status !== 'completed' || workflow?.threadId !== target.threadId ||
      lifecycle?.application.id !== current.application.id || lifecycle.initialization.threadId !== target.threadId ||
      lifecycle.revision <= current.lifecycle.revision)
    throw new Error('阶段交接未返回有效的目标线程与生命周期。')
  return workflow
}

import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { ChatSessionDevelopmentTarget } from '../../service/chatSessions'
import type { AgentChatMessage } from './types'
import { isConversationWorkflow } from './conversationMode'
import { workflowEndedPlanControl } from './planExecutionMode'
import { workflowClarification } from './components/WorkflowRunCard/workflowClarification'
import type { QuickTaskItem } from './components/QuickTaskGuide/quickTasks'

export type DevelopmentNextStep = {
  key: string
  reason: 'completed' | 'ended'
  endedRunId?: string
  workspaceUnchanged?: boolean
  target?: ChatSessionDevelopmentTarget
}

/** 从当前正式 Workflow 的显式目标读取任务上下文，不根据自然语言或消息标题猜测。 */
export function workflowDevelopmentTarget(
  workflow: WorkflowRunPayload
): ChatSessionDevelopmentTarget | undefined {
  const scope = workflow.summary.buildExecutionScope
  const target = workflowClarification(workflow)?.testTarget || workflow.summary.testTarget
  const label = target?.label || ''
  if (scope?.type === 'page' && scope.targetId) {
    return { type: 'page', pageId: scope.targetId, label: label || scope.targetId }
  }
  if (scope?.type === 'endpoint' && scope.targetId && scope.apiContractId) {
    return {
      type: 'endpoint',
      apiContractId: scope.apiContractId,
      endpointId: scope.targetId,
      label: label || scope.targetId
    }
  }
  const pageId = workflow.state?.selectedPageId || workflow.result?.selectedPageId
  const endpointId = workflow.state?.selectedEndpointId || workflow.result?.selectedEndpointId
  const apiContractId =
    workflow.state?.selectedApiContractId || workflow.result?.selectedApiContractId
  if (
    typeof endpointId === 'string' &&
    endpointId &&
    typeof apiContractId === 'string' &&
    apiContractId
  ) {
    return { type: 'endpoint', apiContractId, endpointId, label: label || endpointId }
  }
  if (typeof pageId === 'string' && pageId) {
    return { type: 'page', pageId, label: label || pageId }
  }
  return undefined
}

/** 同一可见会话可开发多个目标；重开会话时恢复最近一次正式运行的显式上下文。 */
export function latestDevelopmentTarget(
  messages: AgentChatMessage[]
): ChatSessionDevelopmentTarget | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const workflow = messages[index].workflow
    if (!workflow || isConversationWorkflow(workflow)) continue
    const target = workflowDevelopmentTarget(workflow)
    if (target) return target
  }
  return undefined
}

/** 把页面或 Endpoint 目标转换为快捷任务中的稳定身份。 */
export function developmentTargetTaskId(target?: ChatSessionDevelopmentTarget): string | undefined {
  if (!target) return undefined
  return target.type === 'page'
    ? `page:${target.pageId}`
    : `endpoint:${target.apiContractId}:${target.endpointId}`
}

/** 只由最新正式运行的开发完成事实或成功 End 回执生成下一步，不把暂停和失败当成结束。 */
export function developmentNextStep(
  messages: AgentChatMessage[],
  lifecycle?: ApplicationLifecycle
): DevelopmentNextStep | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const workflow = messages[index].workflow
    if (!workflow) continue
    if (isConversationWorkflow(workflow)) {
      // 已完成的闲聊/只读问答不消费引导；待确认修改、失败或暂停的对话应保留自己的当前入口。
      if (
        workflow.summary.status === 'completed' &&
        ['casual_chat', 'workspace_question'].includes(String(workflow.summary.intent))
      )
        continue
      return undefined
    }
    if (workflowEndedPlanControl(workflow) && workflow.summary.status === 'completed') {
      const control =
        workflow.summary.planControl || workflow.state?.planControl || workflow.result?.planControl
      const targetRunId = (control as { targetRunId?: string }).targetRunId
      const source = messages
        .slice(0, index)
        .reverse()
        .find((message) => message.workflow?.runId === targetRunId)
      return {
        key: workflow.runId,
        reason: 'ended',
        endedRunId: targetRunId,
        workspaceUnchanged: targetRunId ? lifecycle?.endedExecutions?.[targetRunId]?.workspaceUnchanged : undefined,
        target: source?.workflow ? workflowDevelopmentTarget(source.workflow) : undefined
      }
    }
    // 明确放弃待确认规划同样结束本次开发；过期、已确认或无草稿的回执不冒充成功放弃。
    const abandon = workflow.state?.buildTaskPlanAbandon as
      | { status?: string; planningRunId?: string }
      | undefined
    if (
      workflow.summary.phase === 'build_task_plan_abandon' &&
      workflow.summary.status === 'completed' &&
      abandon?.status === 'abandoned'
    ) {
      const source = messages
        .slice(0, index)
        .reverse()
        .find(
          (message) =>
            message.workflow &&
            workflowClarification(message.workflow)?.draftIdentity?.planningRunId ===
              abandon.planningRunId
        )
      return {
        key: workflow.runId,
        reason: 'ended',
        target: source?.workflow ? workflowDevelopmentTarget(source.workflow) : undefined
      }
    }
    // 继续在入场前失败时归档尚未消费，保留原任务入口，避免只能重试一个不存在的执行。
    const ended = workflow.summary.status === 'failed'
      ? Object.values(lifecycle?.endedExecutions || {}).find((item) => item.execution.threadId === workflow.threadId)
      : undefined
    if (ended) {
      const source = messages.find((item) => item.workflow?.runId === ended.execution.runId)?.workflow
      return { key: workflow.runId, reason: 'ended', endedRunId: ended.execution.runId,
        workspaceUnchanged: ended.workspaceUnchanged, target: source ? workflowDevelopmentTarget(source) : undefined }
    }
    // 新正式运行或控制请求已经取代上一项；不能回溯旧完成卡并再次推荐。
    const target = workflowDevelopmentTarget(workflow)
    if (!target) return undefined
    const progress =
      target.type === 'page'
        ? lifecycle?.developmentArtifacts?.pages[target.pageId]
        : lifecycle?.developmentArtifacts?.endpoints[target.apiContractId]?.[target.endpointId]
    const phase = workflow.summary.phase || workflow.result?.phase
    const complete =
      progress?.initialDevelopmentStatus === 'completed' &&
      ['requires_user_input', 'completed'].includes(workflow.summary.status || '') &&
      (phase === 'test_phase_confirmation' || phase === 'finalize_project') &&
      (progress.completedRunId === workflow.runId ||
        progress.completedThreadId === workflow.threadId)
    return complete ? { key: workflow.runId, reason: 'completed', target } : undefined
  }
  return undefined
}

/** 按正式目录顺序推荐其他待开发页面，再推荐接口；已完成和正在开发的目标不会被推荐。 */
export function nextDevelopmentTask(
  tasks: QuickTaskItem[],
  target?: ChatSessionDevelopmentTarget
): QuickTaskItem | undefined {
  const currentId = developmentTargetTaskId(target)
  return tasks.find(
    (task) =>
      task.id !== currentId &&
      task.kind !== 'entity' &&
      task.progress?.initialDevelopmentStatus === 'pending'
  )
}

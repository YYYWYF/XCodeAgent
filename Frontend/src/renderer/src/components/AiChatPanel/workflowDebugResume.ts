import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { AgentChatMessage } from './types'
import { workflowEndedPlanControl } from './planExecutionMode'

/** 沿当前会话的未入场失败回执定位最近一次 End，已入场执行或登记事实会阻断回溯。 */
export function workflowDebugEndedControl(
  active: WorkflowRunPayload | undefined,
  messages: AgentChatMessage[],
  lifecycle: ApplicationLifecycle | undefined
): WorkflowRunPayload | undefined {
  if (!active) return undefined
  if (workflowEndedPlanControl(active)) return active
  const executions = Object.values(lifecycle?.activeExecutions || {})
  /** 只有没有运行/节点开始事件、也未登记到 lifecycle 的失败请求才属于入场前拒绝。 */
  const rejectedBeforeEntry = (workflow: WorkflowRunPayload): boolean =>
    workflow.summary.status === 'failed' &&
    !executions.some((execution) => execution.runId === workflow.runId) &&
    !workflow.events.some((event) =>
      ['workflow.run.started', 'workflow.node.started', 'workflow.node.completed'].includes(
        event.type
      )
    )
  if (!rejectedBeforeEntry(active)) return undefined
  const reverseIndex = [...messages]
    .reverse()
    .findIndex((message) => message.workflow?.runId === active.runId)
  const activeIndex = reverseIndex >= 0 ? messages.length - reverseIndex - 1 : -1
  const previous = activeIndex >= 0 ? messages.slice(0, activeIndex) : messages
  for (const message of [...previous].reverse()) {
    const workflow = message.workflow
    if (!workflow) continue
    if (workflowEndedPlanControl(workflow)) return workflow
    if (!rejectedBeforeEntry(workflow)) return undefined
  }
  return undefined
}

/** 为节点调试选择被结束的业务 Graph 或仍登记的 Build 执行，保持精确运行归属。 */
export function workflowDebugResumeSource(
  active: WorkflowRunPayload | undefined,
  messages: AgentChatMessage[],
  lifecycle: ApplicationLifecycle | undefined,
  node: string
): WorkflowRunPayload | undefined {
  const ended = workflowDebugEndedControl(active, messages, lifecycle)
  if (ended) {
    const control = ended.summary.planControl as { targetRunId?: string } | undefined
    // End 回执的 Run/thread 归属控制请求；节点调试仍从被结束的业务 Graph 读取上下文。
    const source = [...messages]
      .reverse()
      .find(
        (message) =>
          message.workflow?.runId === control?.targetRunId &&
          !workflowEndedPlanControl(message.workflow)
      )?.workflow
    return source || ended
  }
  if (!['unit_test', 'unit_test_repair', 'test_phase_confirmation'].includes(node)) return active
  const workflows = messages.flatMap((message) => (message.workflow ? [message.workflow] : []))
  const build = [...workflows].reverse().find((workflow) => workflow.summary.buildSummary)
  if (!build) return active
  // 选择最近一次 Build，即使它失败也不回退到更早的成功 Build；完成证据仍由后端校验。
  const execution = Object.values(lifecycle?.activeExecutions || {}).find(
    (item) => item.threadId === build.threadId
  )
  if (!execution) return active
  const latest = [...workflows].reverse().find((workflow) => workflow.threadId === build.threadId)
  return latest ? { ...latest, runId: execution.runId } : active
}

import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { AgentChatMessage } from './types'

/** 为下游节点调试选择当前会话仍登记在生命周期中的 Build 执行，不使用会话聊天 thread。 */
export function workflowDebugResumeSource(
  active: WorkflowRunPayload | undefined,
  messages: AgentChatMessage[],
  lifecycle: ApplicationLifecycle | undefined,
  node: string
): WorkflowRunPayload | undefined {
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

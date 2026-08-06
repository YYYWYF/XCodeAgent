import type { WorkflowRunPayload } from '../../typings'

// 判断当前应用规划是否已经进入可展示的用户交互阶段，避免被仍在收尾的传输状态遮挡。
export function planningWorkflowRequiresUserInput(workflow?: WorkflowRunPayload): boolean {
  if (workflow?.summary.status === 'requires_user_input') return true
  for (const source of [workflow?.result, workflow?.state]) {
    const clarification =
      source?.clarification && typeof source.clarification === 'object'
        ? (source.clarification as Record<string, unknown>)
        : undefined
    if (clarification?.status === 'requires_user_input') return true
  }
  return false
}

// 优先读取最新开始执行的节点，兼容摘要仍停留在上一节点的流式快照。
export function planningWorkflowPhase(workflow?: WorkflowRunPayload): string {
  const events = workflow?.events || []
  const lastEvent = events.length ? events[events.length - 1] : undefined
  if (lastEvent?.type === 'workflow.node.started') {
    const startedPhase = lastEvent.nodeName || lastEvent.node?.id
    if (startedPhase) return String(startedPhase)
  }
  return String(workflow?.summary.phase || '')
}

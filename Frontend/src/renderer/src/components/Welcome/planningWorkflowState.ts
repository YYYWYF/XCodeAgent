import type { WorkflowRunPayload } from '../../typings'

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

import type { WorkflowClarification, WorkflowRunPayload } from '../../../../typings'

/** 从 Workflow payload 的多个位置读取待确认载荷，兼容流式快照、最终结果和自定义事件。 */
export function workflowClarification(
  workflow: WorkflowRunPayload
): WorkflowClarification | undefined {
  const fromSummary = workflow.summary.clarification
  if (fromSummary && typeof fromSummary === 'object') return fromSummary

  const stateClarification = workflow.state?.clarification
  if (stateClarification && typeof stateClarification === 'object') {
    return stateClarification as WorkflowClarification
  }

  const resultClarification = workflow.result?.clarification
  if (resultClarification && typeof resultClarification === 'object') {
    return resultClarification as WorkflowClarification
  }

  const clarificationEvent = workflow.events
    .slice()
    .reverse()
    .find((event) => {
      const detail = event.data?.detail
      return Boolean(detail && typeof detail === 'object' && 'clarification' in detail)
    })
  const eventClarification = clarificationEvent?.data?.detail
  if (
    eventClarification &&
    typeof eventClarification === 'object' &&
    'clarification' in eventClarification
  ) {
    const clarification = (eventClarification as { clarification?: unknown }).clarification
    if (clarification && typeof clarification === 'object') {
      return clarification as WorkflowClarification
    }
  }

  return undefined
}

import type { WorkflowRunPayload, WorkflowProductConversationResult } from '../../../../typings'
import type { AgentChatMessage } from '../../types'

/** 从公开 Workflow 投影中读取规范化后的产品对话结果。 */
export function workflowProductConversationResult(
  workflow?: WorkflowRunPayload
): WorkflowProductConversationResult | undefined {
  const candidates = [
    workflow?.summary?.productConversationResult,
    workflow?.result?.productConversationResult,
    workflow?.result?.product_conversation_result,
    workflow?.state?.productConversationResult,
    workflow?.state?.product_conversation_result
  ]
  return candidates.find(
    (value): value is WorkflowProductConversationResult =>
      Boolean(value) &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      typeof (value as WorkflowProductConversationResult).mutating === 'boolean'
  )
}

/** 判断本轮是否只新增产品 Agent 回复并保留现有产物展示。 */
export function isNonMutatingProductConversation(workflow?: WorkflowRunPayload): boolean {
  const result = workflowProductConversationResult(workflow)
  return (
    result?.mutating === false &&
    result.presentation?.artifactPresentation === 'preserve'
  )
}

/** 按产物、门禁和 revision 生成稳定审阅卡身份，不使用 runId 或消息 id。 */
export function planningReviewIdentity(workflow?: WorkflowRunPayload): string | undefined {
  const candidates = [
    workflow?.result?.application_planning_interrupt,
    workflow?.result?.applicationPlanningInterrupt,
    workflow?.state?.application_planning_interrupt,
    workflow?.state?.applicationPlanningInterrupt
  ]
  const interrupt = candidates.find(
    (value) => value && typeof value === 'object' && !Array.isArray(value)
  ) as Record<string, unknown> | undefined
  if (!interrupt) return undefined
  const artifact = String(interrupt.artifact || '').trim()
  const gateId = String(interrupt.gateId || '').trim()
  const artifactRevision = String(interrupt.artifactRevision || '').trim()
  if (!artifact || !gateId || !artifactRevision) return undefined
  return `${artifact}:${gateId}:${artifactRevision}`
}

/** 只按当前 planning checkpoint 身份判断历史卡是否仍为有效审阅门。 */
export function planningReviewMatchesActiveWorkflow(
  reviewWorkflow: WorkflowRunPayload | undefined,
  activeWorkflow: WorkflowRunPayload | undefined
): boolean {
  const reviewIdentity = planningReviewIdentity(reviewWorkflow)
  return Boolean(reviewIdentity && reviewIdentity === planningReviewIdentity(activeWorkflow))
}

/** 为每个稳定审阅身份选择历史中最早的卡片宿主，后续恢复轮只更新状态不追加卡。 */
export function canonicalPlanningReviewMessageIndexes(
  messages: AgentChatMessage[]
): Map<string, number> {
  const result = new Map<string, number>()
  messages.forEach((message, index) => {
    if (message.role !== 'assistant') return
    const identity = planningReviewIdentity(message.workflow)
    if (identity && !result.has(identity)) result.set(identity, index)
  })
  return result
}

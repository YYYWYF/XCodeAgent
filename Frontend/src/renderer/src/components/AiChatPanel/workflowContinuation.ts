import type { WorkflowClarification, WorkflowClarificationAnswers } from '../../typings'

/** 为页面最终验收生成不依赖问题列表的稳定继续消息。 */
export function pageAcceptanceContinuationMessage(
  clarification: WorkflowClarification | undefined,
  answers: WorkflowClarificationAnswers
): string {
  if (clarification?.mode !== 'page_acceptance' || answers.page_acceptance !== 'accepted') {
    return ''
  }
  return '已完成页面预览，确认验收通过并完成计划。'
}

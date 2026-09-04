import type { WorkflowClarification, WorkflowClarificationAnswers } from '../../typings'

/** 为页面最终验收生成不依赖问题列表的稳定继续消息。 */
export function pageAcceptanceContinuationMessage(
  clarification: WorkflowClarification | undefined,
  answers: WorkflowClarificationAnswers
): string {
  if (clarification?.mode !== 'page_acceptance' || answers.page_acceptance !== 'accepted') {
    return ''
  }
  return '已完成产物审查，确认验收通过并完成计划。'
}

/** 为执行方式选择节点生成稳定继续消息；答案携带所选执行通道。 */
export function backgroundDispatchContinuationMessage(
  clarification: WorkflowClarification | undefined,
  answers: WorkflowClarificationAnswers
): string {
  if (clarification?.mode !== 'background_dispatch') return ''
  // 页面轮与接口轮分别提交（background_dispatch / background_dispatch_endpoint），取先出现的答案。
  const pool = String(answers.background_dispatch ?? answers.background_dispatch_endpoint ?? '')
  if (pool === 'sync') return '已选择同步任务，将在当前任务中直接执行。'
  if (pool === 'tide') return '已选择潮汐任务，实现任务已加入闲时算力队列后台执行。'
  if (pool === 'async') return '已选择异步任务，实现任务已加入常规算力队列后台执行。'
  return ''
}

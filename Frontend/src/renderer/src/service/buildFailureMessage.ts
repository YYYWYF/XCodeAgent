import type { WorkflowRunPayload } from '../typings'

/** 从当前失败 Build 的任务事实归因，防止权限初始化等上游文案污染恢复提示。 */
export function currentBuildFailureMessage(workflow?: WorkflowRunPayload): string | undefined {
  if (workflow?.summary.status !== 'failed' || workflow.summary.buildSummary?.status !== 'failed')
    return undefined
  const event = [...workflow.events]
    .reverse()
    .find((item) => item.type === 'workflow.node.completed' && item.nodeName !== 'handle_failure')
  if (event?.nodeName !== 'build' || event.status !== 'failed') return undefined
  const detail = event.data?.detail as Record<string, unknown> | undefined
  const summary = detail?.buildSummary as Record<string, unknown> | undefined
  if (typeof summary?.finalization_error === 'string' && summary.finalization_error.trim())
    return summary.finalization_error.trim()
  const slice = detail?.buildExecutionSlice as Record<string, unknown> | undefined
  const tasks = Array.isArray(slice?.tasks) ? (slice.tasks as Record<string, unknown>[]) : []
  const reasons = tasks
    .filter(
      (task) =>
        task.status === 'failed' &&
        typeof task.failure_reason === 'string' &&
        task.failure_reason.trim()
    )
    .slice(0, 3)
    .map(
      (task) =>
        `${typeof task.id === 'string' ? task.id : '构建任务'}：${String(task.failure_reason).trim().slice(0, 400)}`
    )
  return reasons.length ? `代码构建失败：${reasons.join('；')}` : undefined
}

/** 仅纠正与当前旧摘要相同的误归因文本，独立重试错误保持原样。 */
export function buildFailureMessageForDisplay(
  message: string | undefined,
  workflow?: WorkflowRunPayload
): string | undefined {
  const current = currentBuildFailureMessage(workflow)
  return current && message?.trim() === workflow?.summary.message?.trim() ? current : message
}

import type { WorkflowBuildExecutionSlice, WorkflowRunPayload } from '../typings'
import type { ProcessStepRecord } from './agUiAgent'

/** 用当前 Workflow 的任务切片补齐 Build 步骤，不从历史任务计划推测执行结果。 */
export function restoreBuildExecutionStep(
  steps: ProcessStepRecord[] | undefined,
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord[] | undefined {
  const slice = workflow?.state?.buildExecutionSlice as WorkflowBuildExecutionSlice | undefined
  if (!workflow || !slice || !Array.isArray(slice.tasks)) return steps
  const current = steps || []
  const build = [...current]
    .reverse()
    .find((step) => step.nodeName === 'build' || step.id === 'workflow:build')
  // 活跃步骤由实时进度帧拥有；新 Run 尚未执行 Build 时不能引入来源 Run 的旧切片。
  if (workflow.summary.status === 'running' && build?.buildExecutionSlice) return steps
  const hasCurrentRunStart = workflow.events.some(
    (event) => event.type === 'workflow.run.started' || event.type === 'workflow.node.started'
  )
  if (!build && hasCurrentRunStart && workflow.summary.phase !== 'build') return steps

  const summaryStatus = slice.summary?.status || workflow.summary.buildSummary?.status
  const status: ProcessStepRecord['status'] =
    summaryStatus === 'awaiting_task_retry' || summaryStatus === 'requires_confirmation'
      ? 'requires_user_input'
      : workflow.summary.phase === 'build' && workflow.summary.status === 'failed'
        ? 'failed'
        : summaryStatus === 'completed'
          ? 'completed'
          : summaryStatus === 'failed'
            ? 'failed'
            : workflow.summary.phase === 'build' &&
                workflow.summary.status === 'requires_user_input'
              ? 'requires_user_input'
              : build?.status || 'running'
  const title =
    status === 'requires_user_input'
      ? summaryStatus === 'awaiting_task_retry'
        ? '等待处理 构建执行'
        : '等待确认 构建执行'
      : status === 'failed'
        ? '执行失败 构建执行'
        : status === 'completed'
          ? '已完成 构建执行'
          : '构建执行'
  const recovered: ProcessStepRecord = {
    ...build,
    id: build?.id || 'workflow:build',
    kind: 'workflow',
    nodeName: 'build',
    attempt: build?.attempt || 1,
    status,
    title,
    detail: build?.detail || '',
    sequence: build?.sequence ?? Math.max(0, ...current.map((step) => step.sequence)) + 1,
    buildExecutionSlice: slice
  }
  return build
    ? current.map((step) => (step === build ? recovered : step))
    : [...current, recovered]
}

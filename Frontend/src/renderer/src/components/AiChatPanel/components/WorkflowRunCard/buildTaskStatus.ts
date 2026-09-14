import type {
  WorkflowBuildExecutionSlice,
  WorkflowBuildExecutionTask
} from '../../../../typings'

type BuildTaskDisplayStatus = 'pending' | 'running' | 'completed' | 'failed'

/** 将已实现和经验证已满足要求统一展示为完成，保留服务端原始执行状态。 */
export function buildTaskDisplayStatus(
  status: WorkflowBuildExecutionTask['status']
): BuildTaskDisplayStatus {
  if (status === 'completed' || status === 'already_satisfied') return 'completed'
  if (status === 'running' || status === 'failed') return status
  return 'pending'
}

/** 优先根据完整任务列表判定构建终态，仅在列表缺失或含未知状态时回退到汇总统计。 */
export function buildExecutionTerminalStatus(
  slice: WorkflowBuildExecutionSlice
): 'completed' | 'failed' | undefined {
  const tasks = Array.isArray(slice.tasks) ? slice.tasks : []
  const taskStatuses = tasks.map((task) => String(task.status || ''))
  const knownStatuses = new Set([
    'pending',
    'running',
    'completed',
    'already_satisfied',
    'failed'
  ])
  if (taskStatuses.length > 0 && taskStatuses.every((status) => knownStatuses.has(status))) {
    if (taskStatuses.some((status) => status === 'pending' || status === 'running')) {
      return undefined
    }
    if (taskStatuses.some((status) => status === 'failed')) return 'failed'
    if (taskStatuses.every((status) => status === 'completed' || status === 'already_satisfied')) {
      return 'completed'
    }
  }

  const summary = slice.summary || {}
  const total = Number.isFinite(summary.total) ? Number(summary.total) : tasks.length
  const pending = Number.isFinite(summary.pending)
    ? Number(summary.pending)
    : tasks.filter((task) => task.status === 'pending').length
  const running = Number.isFinite(summary.running)
    ? Number(summary.running)
    : tasks.filter((task) => task.status === 'running').length
  const failed = Number.isFinite(summary.failed)
    ? Number(summary.failed)
    : tasks.filter((task) => task.status === 'failed').length
  const completed = Number.isFinite(summary.completed)
    ? Number(summary.completed)
    : tasks.filter((task) => buildTaskDisplayStatus(task.status) === 'completed').length
  if (total <= 0 || pending > 0 || running > 0) return undefined
  if (failed > 0) return 'failed'
  return completed >= total ? 'completed' : undefined
}

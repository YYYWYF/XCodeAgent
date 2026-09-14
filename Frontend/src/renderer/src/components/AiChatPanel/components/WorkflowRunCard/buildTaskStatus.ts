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

/**
 * 该任务是否形成了可提交的检查点（弱提醒，只做标记不打断）。
 *
 * 要求任务**真正写过文件**：`already_satisfied` 表示"检查后确认无需改动"，它虽然展示为
 * 完成，但工作区没有产生新变更，不该被标成检查点。用 `targetFiles` 作为"本轮写了哪些
 * 文件"的依据 —— 它是任务声明的写入范围，服务端执行前就已确定。
 */
export function isCheckpointCandidate(task: WorkflowBuildExecutionTask): boolean {
  if (task.status !== 'completed') return false
  const targetFiles = task.targetFiles ?? task.target_files ?? []
  return targetFiles.length > 0
}

/**
 * 该构建轮次是否已形成一个可提交的模块级检查点（中等提示）。
 *
 * 条件：本轮次**全部任务都已完成**，但整个工作流**仍在运行** —— 即"这一个模块做完了、
 * 整体还没结束"。这正是文档说的候选提交点：值得让用户知道，但不该弹窗打断后续批次。
 *
 * 轮次自身已经 completed 时不提示：那时整体结果卡会承载收尾语义，再提示是重复。
 *
 * 还要求**至少有一个任务真正写过文件**（沿用 `isCheckpointCandidate` 的口径）：整批都是
 * `already_satisfied` 时本轮没有产生任何新变更，这时说"可创建提交"是空头支票。
 */
export function isModuleCheckpointCandidate(input: {
  executionStatus: 'running' | 'completed' | 'failed' | 'requires_user_input'
  tasks: WorkflowBuildExecutionTask[] | undefined
}): boolean {
  if (input.executionStatus !== 'running') return false
  const tasks = input.tasks ?? []
  if (tasks.length === 0) return false
  if (!tasks.every((task) => buildTaskDisplayStatus(task.status) === 'completed')) return false
  return tasks.some((task) => isCheckpointCandidate(task))
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

import type { WorkflowBuildTaskPlanTask } from '../../../../typings'

const taskStagePrefixes = {
  foundation: '模块基础骨架 · ',
  business: '接口业务实现 · ',
  controller: '模块 Controller · '
}

export type TaskFileChange = {
  path: string
  name: string
  operations: string[]
  descriptions: string[]
}

/** 仅移除与明确阶段一致的固定标题前缀。 */
export function taskDisplayTitle(task: Pick<WorkflowBuildTaskPlanTask, 'title' | 'backendStage'>): string {
  const prefix = task.backendStage && taskStagePrefixes[task.backendStage]
  return prefix && task.title.startsWith(prefix) && task.title.length > prefix.length
    ? task.title.slice(prefix.length)
    : task.title
}

/** 统一路径分隔符并清除首尾空白，用于展示及完整路径去重。 */
function normalizedPath(value: unknown): string {
  return typeof value === 'string' ? value.trim().replace(/\\/g, '/') : ''
}

/** 收集结构化文件改动，目录与通配范围独立保留，不丢失重复文件的不同说明。 */
export function taskFilePresentation(task: WorkflowBuildTaskPlanTask): {
  files: TaskFileChange[]
  ranges: TaskFileChange[]
  supplementaryDescription: string
  repairSupplement: string
} {
  const entries = new Map<string, TaskFileChange>()
  const explicitFiles = new Set((task.target_files || []).map(normalizedPath))

  /** 按声明顺序合并路径及其操作与说明。 */
  function append(pathValue: unknown, operation?: unknown, description?: unknown): void {
    const path = normalizedPath(pathValue)
    if (!path) return
    const segments = path.split('/')
    const name = segments.pop() || path
    const entry = entries.get(path) || {
      path,
      name,
      operations: [],
      descriptions: []
    }
    if (
      typeof operation === 'string' &&
      operation.trim() &&
      !entry.operations.includes(operation)
    ) {
      entry.operations.push(operation)
    }
    if (
      typeof description === 'string' &&
      description.trim() &&
      !entry.descriptions.includes(description)
    ) {
      entry.descriptions.push(description)
    }
    entries.set(path, entry)
  }

  for (const item of task.change_scope || []) append(item.path, item.operation, item.description)
  for (const path of task.target_files || []) append(path)
  for (const path of task.allowed_paths || []) append(path)
  for (const item of task.deliverables || []) {
    if (Array.isArray(item.paths)) for (const path of item.paths) append(path)
  }

  const files: TaskFileChange[] = []
  const ranges: TaskFileChange[] = []
  for (const entry of entries.values()) {
    // target_files 明确声明的无扩展名文件也应计数，范围字段中的目录保持保守展示。
    const isRange =
      /[*?\[\]{}]/.test(entry.path) ||
      entry.path.endsWith('/') ||
      (!explicitFiles.has(entry.path) && !entry.name.includes('.'))
    const destination = isRange ? ranges : files
    destination.push(entry)
  }

  // 只对生成器逐项拼接的完整字符串去重，任何额外任务约束都继续显示。
  const scope = task.change_scope || []
  const generatedDescription = scope
    .map((item, index) => `${index + 1}. ${item.description}（${item.path}）`)
    .join('\n')
  const fullyDescribed =
    scope.length > 0 &&
    scope.every(
      (item) =>
        typeof item.path === 'string' &&
        item.path.trim() &&
        typeof item.description === 'string' &&
        item.description.trim()
    )
  // 修复追加在原描述之后；还原原文件说明后只在精确匹配时沿用去重规则。
  const marker = '\n\n修复补充：'
  const parts = task.description.split(marker)
  const originalDescription = parts[0]
  const originalScopeLines = scope.map((item, index) =>
    `${index + 1}. ${typeof item.description === 'string' ? item.description.split('\n修复补充：')[0] : ''}（${item.path}）`)
  const generatedOriginal = parts.length > 1 && originalScopeLines.some((_, index) =>
    originalScopeLines.slice(0, index + 1).join('\n') === originalDescription)
  return {
    files,
    ranges,
    supplementaryDescription:
      (fullyDescribed && originalDescription === generatedDescription) || generatedOriginal
        ? '' : originalDescription,
    repairSupplement: parts.slice(1).join('\n\n修复补充：')
  }
}

/** 优先解析本轮依赖名称，跨本轮使用后端从正式任务集合投影的名称。 */
export function taskDependencyNames(
  task: WorkflowBuildTaskPlanTask,
  tasksById: ReadonlyMap<string, WorkflowBuildTaskPlanTask>
): string[] {
  const names = (task.dependencies || []).map((id) => {
    const dependency = tasksById.get(id)
    return dependency ? taskDisplayTitle(dependency) : task.dependencyTitles?.[id] || ''
  })
  return [...new Set(names.filter((name) => name.trim()))]
}

import type { QuickTaskItem } from '../QuickTaskGuide/quickTasks'

export type TargetStatusFilter = 'all' | 'pending' | 'in_progress' | 'completed'
export type TargetKindFilter = 'all' | 'page' | 'endpoint'

/** 只有正式目录读取成功且非空的页面/API 全部完成时，才隐藏继续开发引导。 */
export function allDevelopmentTargetsCompleted(
  tasks: QuickTaskItem[],
  loading: boolean,
  catalogError?: string | null
): boolean {
  const targets = tasks.filter((task) => task.kind !== 'entity')
  return (
    !loading &&
    !catalogError &&
    targets.length > 0 &&
    targets.every((task) => task.progress?.initialDevelopmentStatus === 'completed')
  )
}

/** 联合应用状态、类型和搜索条件；缺失进度不伪装为未开发或已完成。 */
export function filterDevelopmentTargets(
  tasks: QuickTaskItem[],
  status: TargetStatusFilter,
  kind: TargetKindFilter,
  search: string
): QuickTaskItem[] {
  const query = search.trim().toLocaleLowerCase()
  return tasks.filter(
    (task) =>
      task.kind !== 'entity' &&
      (kind === 'all' || task.kind === kind) &&
      (status === 'all' || task.progress?.initialDevelopmentStatus === status) &&
      (!query ||
        `${task.title} ${task.meta} ${task.kind === 'endpoint' ? task.endpointPath : ''} ${task.description}`
          .toLocaleLowerCase()
          .includes(query))
  )
}

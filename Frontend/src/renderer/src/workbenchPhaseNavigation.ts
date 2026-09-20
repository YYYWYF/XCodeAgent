import type { WorkbenchPhase } from './workbenchPhase'

export const WORKBENCH_PHASE_ORDER: readonly WorkbenchPhase[] = [
  'product',
  'planning',
  'development',
  'test',
  'review',
  'acceptance'
]
const REACHED_PHASE_PREFIX = 'xcodeagent:workbench-reached-phase:'

/** 合并应用已到达阶段；浏览上游页面或运行结束都不会降低可回访范围。 */
export function furthestWorkbenchPhase(...phases: WorkbenchPhase[]): WorkbenchPhase {
  return phases.reduce(
    (furthest, phase) =>
      WORKBENCH_PHASE_ORDER.indexOf(phase) > WORKBENCH_PHASE_ORDER.indexOf(furthest)
        ? phase
        : furthest,
    'product'
  )
}

/** 删除指定前缀的全部本地存储键；先收集再删除，避免边遍历边改动。 */
export function removeLocalStorageKeysWithPrefix(prefix: string): void {
  const matched: string[] = []
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index)
    if (key && key.startsWith(prefix)) matched.push(key)
  }
  matched.forEach((key) => window.localStorage.removeItem(key))
}

/**
 * 浏览进度按「应用 + 版本」隔离：发起新迭代会开出一条全新的旅程，
 * 上一版本的审查/验收到达事实不能给新迭代解锁回访入口。
 */
function reachedPhaseStorageKey(applicationId: string, versionId: string): string {
  return `${REACHED_PHASE_PREFIX}${applicationId}:${versionId}`
}

/** 读取当前迭代的浏览进度；该记录不授予任何工作流执行权限。 */
export function getReachedWorkbenchPhase(applicationId: string, versionId: string): WorkbenchPhase {
  const stored = window.localStorage.getItem(reachedPhaseStorageKey(applicationId, versionId))
  return WORKBENCH_PHASE_ORDER.find((phase) => phase === stored) || 'product'
}

/** 单调保存最远浏览阶段，避免手动回退覆盖已经到达的审查或验收。 */
export function recordReachedWorkbenchPhase(
  applicationId: string,
  versionId: string,
  phase: WorkbenchPhase
): WorkbenchPhase {
  const reached = furthestWorkbenchPhase(getReachedWorkbenchPhase(applicationId, versionId), phase)
  window.localStorage.setItem(reachedPhaseStorageKey(applicationId, versionId), reached)
  return reached
}

/** 删除应用时同步清理其全部版本的浏览进度。 */
export function clearReachedWorkbenchPhase(applicationId: string): void {
  removeLocalStorageKeysWithPrefix(`${REACHED_PHASE_PREFIX}${applicationId}:`)
}

/** 从当前会话目录恢复可回访范围，忽略尚未开始的空会话，不扫描消息正文。 */
export function reachedPhaseFromSessions(
  sessions: readonly { workbenchPhase: WorkbenchPhase; messageCount: number }[]
): WorkbenchPhase {
  return furthestWorkbenchPhase(
    ...sessions
      .filter((session) => session.messageCount > 0)
      .map((session) => session.workbenchPhase)
  )
}

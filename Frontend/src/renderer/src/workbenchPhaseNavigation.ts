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

/** 读取应用独立的浏览进度；该记录不授予任何工作流执行权限。 */
export function getReachedWorkbenchPhase(applicationId: string): WorkbenchPhase {
  const stored = window.localStorage.getItem(`${REACHED_PHASE_PREFIX}${applicationId}`)
  return WORKBENCH_PHASE_ORDER.find((phase) => phase === stored) || 'product'
}

/** 单调保存最远浏览阶段，避免手动回退覆盖已经到达的审查或验收。 */
export function recordReachedWorkbenchPhase(
  applicationId: string,
  phase: WorkbenchPhase
): WorkbenchPhase {
  const reached = furthestWorkbenchPhase(getReachedWorkbenchPhase(applicationId), phase)
  window.localStorage.setItem(`${REACHED_PHASE_PREFIX}${applicationId}`, reached)
  return reached
}

/** 删除应用时同步清理其阶段浏览进度。 */
export function clearReachedWorkbenchPhase(applicationId: string): void {
  window.localStorage.removeItem(`${REACHED_PHASE_PREFIX}${applicationId}`)
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

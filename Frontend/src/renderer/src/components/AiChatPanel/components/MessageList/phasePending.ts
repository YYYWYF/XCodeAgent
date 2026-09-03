import type { WorkbenchPhase } from '../../../../workbenchPhase'

/** 返回阶段空白会话的过渡文案，避免阶段专属会话短暂回退到通用任务入口。 */
export function phasePendingDetail(phase: WorkbenchPhase): string | undefined {
  if (phase === 'test') return '正在准备进入测试阶段…'
  if (phase === 'review') return '正在准备进入审查阶段…'
  if (phase === 'acceptance') return '正在启动项目准备验收…'
  return undefined
}

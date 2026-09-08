import type { DevelopmentArtifactProgress, TestEntryGate } from './typings'
import type { WorkbenchPhase } from './workbenchPhase'

/** 根据权威门禁约束测试视图，覆盖手动选择、冷启动恢复与自动阶段推导。 */
export function gateWorkbenchPhase(phase: WorkbenchPhase, gate?: TestEntryGate): WorkbenchPhase {
  return phase === 'test' && gate?.allowed !== true ? 'development' : phase
}

/** 为尚未加载及被门禁阻断的测试入口提供一致说明。 */
export function testEntryGateReason(gate?: TestEntryGate): string {
  return gate?.reason || (gate ? '' : '正在读取开发产物状态…')
}

/** 为圆点提供可访问的状态说明，未登记产物一律不视为完成。 */
export function developmentStatusLabel(progress?: DevelopmentArtifactProgress): string {
  if (progress?.initialDevelopmentStatus === 'completed') return '初次开发已完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '初次开发中（可能正在等待确认）'
  return '初次开发未完成'
}

/** 统计完整分组中的完成项，不使用搜索或折叠后的可见集合。 */
export function developmentCompletedCount(
  records: (DevelopmentArtifactProgress | undefined)[]
): number {
  return records.filter((record) => record?.initialDevelopmentStatus === 'completed').length
}

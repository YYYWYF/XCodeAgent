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

/** 底部控制栏在测试确认阶段使用的门禁感知文案。 */
export type TestPhaseDockCopy = {
  title: string
  description: string
  interaction: string
}

/** 底部控制栏只在全量门禁放行后才引导进入测试，避免未完成产物仍显示开发已完成。 */
export function testPhaseDockCopy(gate?: TestEntryGate): TestPhaseDockCopy {
  if (gate?.allowed === true) {
    return {
      title: '等待进入测试阶段',
      description: '开发已完成，请在上方确认进入测试阶段。',
      interaction: '开发已完成，请在上方确认进入测试阶段。'
    }
  }
  return {
    title: '当前产物开发已完成',
    description: testEntryGateReason(gate),
    interaction: '请在上方未完成产物中点「去开发」，或新建对话后选择其余产物。'
  }
}

/** 为圆点提供可访问的状态说明，未登记产物一律不视为完成。 */
export function developmentStatusLabel(progress?: DevelopmentArtifactProgress): string {
  if (progress?.initialDevelopmentStatus === 'completed') return '初次开发已完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '初次开发中（可能正在等待确认）'
  return '初次开发未完成'
}

/** 为新会话产物卡片提供简短且统一的开发状态文案。 */
export function developmentStatusText(progress?: DevelopmentArtifactProgress): string {
  if (progress?.initialDevelopmentStatus === 'completed') return '已初次完成'
  if (progress?.initialDevelopmentStatus === 'in_progress') return '开发中'
  return '未开发'
}

/** 统计完整分组中的完成项，不使用搜索或折叠后的可见集合。 */
export function developmentCompletedCount(
  records: (DevelopmentArtifactProgress | undefined)[]
): number {
  return records.filter((record) => record?.initialDevelopmentStatus === 'completed').length
}

import { createContext, useContext } from 'react'
import type { TestEntryGate } from '../typings'
import type {
  EditableObjectType,
  WorkbenchAgentIdentity,
  WorkbenchPhase,
  WorkbenchPhaseOverrideSource
} from '../workbenchPhase'

export type WorkbenchPhaseContextValue = {
  testEntryGate?: TestEntryGate
  /** 实际生效阶段已通过测试门禁。 */
  phase: WorkbenchPhase
  derivedPhase: WorkbenchPhase
  /** 独立于当前浏览视图的应用最远阶段，仅用于回访入口。 */
  reachedPhase: WorkbenchPhase
  recordReachedPhase: (phase: WorkbenchPhase) => void
  manualOverride: WorkbenchPhase | null
  /** source 只用于诊断记录（区分用户点击与平台自动锁），不参与判定。 */
  switchPhase: (phase: WorkbenchPhase | null, source?: WorkbenchPhaseOverrideSource) => void
  agent: WorkbenchAgentIdentity
  canEdit: (objectType: EditableObjectType) => boolean
  /** 已发布版本或非活跃版本：阶段切换与 Agent 调度锁定，只能回看。 */
  locked: boolean
}

export const WorkbenchPhaseContext = createContext<WorkbenchPhaseContextValue | null>(null)

/** 读取工作台阶段与入口门禁，缺失 Provider 时立即报告接线错误。 */
export function useWorkbenchPhase(): WorkbenchPhaseContextValue {
  const context = useContext(WorkbenchPhaseContext)
  if (!context) throw new Error('useWorkbenchPhase must be used within WorkbenchPhaseProvider')
  return context
}

/** 历史确认卡订阅当前应用门禁；未挂载工作台时入口保持关闭。 */
export function useTestEntryGate(): TestEntryGate | undefined {
  return useContext(WorkbenchPhaseContext)?.testEntryGate
}

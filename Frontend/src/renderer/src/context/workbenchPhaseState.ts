import { createContext, useContext } from 'react'
import type { TestEntryGate } from '../typings'
import type { EditableObjectType, WorkbenchAgentIdentity, WorkbenchPhase } from '../workbenchPhase'

export type WorkbenchPhaseContextValue = {
  testEntryGate?: TestEntryGate
  /** 实际生效阶段已通过测试门禁。 */
  phase: WorkbenchPhase
  derivedPhase: WorkbenchPhase
  manualOverride: WorkbenchPhase | null
  switchPhase: (phase: WorkbenchPhase | null) => void
  agent: WorkbenchAgentIdentity
  canEdit: (objectType: EditableObjectType) => boolean
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

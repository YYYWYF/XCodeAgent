import { type ReactNode, useEffect, useMemo, useState } from 'react'
import type { ApplicationLifecycle } from '../typings'
import { WorkbenchPhaseContext, type WorkbenchPhaseContextValue } from './workbenchPhaseState'
import { gateWorkbenchPhase } from '../developmentArtifacts'
import {
  deriveWorkbenchPhase,
  getPersistedWorkbenchPhase,
  isObjectEditableInPhase,
  resolveWorkbenchPhase,
  setPersistedWorkbenchPhase,
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../workbenchPhase'

/**
 * 按应用隔离的手动阶段覆盖。旅程向前自动推进阶段（derivedPhase）；
 * 用户手动切回（例如切到产品做增量迭代）会覆盖该值，传 null 恢复跟随旅程。
 */
export function WorkbenchPhaseProvider({
  applicationId,
  lifecycle,
  children
}: {
  applicationId: string
  lifecycle?: ApplicationLifecycle
  children: ReactNode
}): JSX.Element {
  const testEntryGate = lifecycle?.testEntryGate
  const derivedPhase = gateWorkbenchPhase(deriveWorkbenchPhase(lifecycle), testEntryGate)
  // 恢复用户上次手动选择的阶段；未覆盖时始终跟随后端生命周期。
  const [overrides, setOverrides] = useState<Record<string, WorkbenchPhase | null>>(() => {
    const persistedPhase = getPersistedWorkbenchPhase(applicationId)
    return persistedPhase ? { [applicationId]: persistedPhase } : {}
  })
  const manualOverride = overrides[applicationId] ?? null
  useEffect(() => {
    // 确认门禁关闭后清除旧测试选择，避免最后一个产物完成时自动跳回测试视图。
    if (testEntryGate && !testEntryGate.allowed && manualOverride === 'test') {
      setPersistedWorkbenchPhase(applicationId, 'development')
      setOverrides((current) => ({ ...current, [applicationId]: 'development' }))
    }
  }, [applicationId, manualOverride, testEntryGate])

  const value = useMemo<WorkbenchPhaseContextValue>(() => {
    const phase = gateWorkbenchPhase(
      resolveWorkbenchPhase(derivedPhase, manualOverride),
      testEntryGate
    )
    return {
      testEntryGate,
      phase,
      derivedPhase,
      manualOverride,
      switchPhase: (next) => {
        if (next === 'test' && testEntryGate?.allowed !== true) return
        // 只持久化用户明确的界面覆盖；传 null 表示恢复生命周期自动阶段。
        setPersistedWorkbenchPhase(applicationId, next)
        setOverrides((current) => ({ ...current, [applicationId]: next ?? null }))
      },
      agent: WORKBENCH_PHASE_AGENTS[phase],
      canEdit: (objectType) => isObjectEditableInPhase(objectType, phase)
    }
  }, [applicationId, manualOverride, derivedPhase, testEntryGate])

  return <WorkbenchPhaseContext.Provider value={value}>{children}</WorkbenchPhaseContext.Provider>
}

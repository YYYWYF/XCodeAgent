import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import type { ApplicationLifecycle } from '../typings'
import { WorkbenchPhaseContext, type WorkbenchPhaseContextValue } from './workbenchPhaseState'
import { gateWorkbenchPhase } from '../developmentArtifacts'
import {
  furthestWorkbenchPhase,
  getReachedWorkbenchPhase,
  recordReachedWorkbenchPhase
} from '../workbenchPhaseNavigation'
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
 * 按「应用 + 版本」隔离的手动阶段覆盖与浏览进度。旅程向前自动推进阶段（derivedPhase）；
 * 用户手动切回（例如切到产品做增量迭代）会覆盖该值，传 null 恢复跟随旅程。
 * versionId 变化（发起新迭代/切换查看版本）即视为新旅程，不复用上一轮的选择。
 */
export function WorkbenchPhaseProvider({
  applicationId,
  versionId,
  lifecycle,
  locked = false,
  children
}: {
  applicationId: string
  versionId: string
  lifecycle?: ApplicationLifecycle
  locked?: boolean
  children: ReactNode
}): JSX.Element {
  const testEntryGate = lifecycle?.testEntryGate
  const derivedPhase = gateWorkbenchPhase(deriveWorkbenchPhase(lifecycle), testEntryGate)
  // 恢复用户在当前迭代里手动选择的阶段；未覆盖时始终跟随后端生命周期。
  const [overrides, setOverrides] = useState<Record<string, WorkbenchPhase | null>>(() => {
    const persistedPhase = getPersistedWorkbenchPhase(applicationId, versionId)
    return persistedPhase ? { [applicationId]: persistedPhase } : {}
  })
  const manualOverride = overrides[applicationId] ?? null
  const phase = gateWorkbenchPhase(
    resolveWorkbenchPhase(derivedPhase, manualOverride),
    testEntryGate
  )
  const [reachedByApplication, setReachedByApplication] = useState<Record<string, WorkbenchPhase>>(
    {}
  )
  // 到达记录同样按「应用 + 版本」隔离。只按应用存会让上一版本的最远阶段泄漏进新迭代：
  // 看 v1.0（推导出验收）之后切回刚发起的 v1.2，会带着验收/开发的到达事实，
  // 于是本不该可点的计划/开发页签被解锁，用户点进去还会把该值写进新版本的存储。
  const reachedScopeKey = `${applicationId}:${versionId}`
  const reachedPhase = furthestWorkbenchPhase(
    reachedByApplication[reachedScopeKey] ?? getReachedWorkbenchPhase(applicationId, versionId),
    derivedPhase,
    phase
  )
  /** 运行推进和当前会话目录均可补充到达记录，切换视图只会扩大而不会缩小范围。 */
  const recordReachedPhase = useCallback(
    (next: WorkbenchPhase): void => {
      const reached = recordReachedWorkbenchPhase(applicationId, versionId, next)
      setReachedByApplication((current) =>
        current[reachedScopeKey] === reached ? current : { ...current, [reachedScopeKey]: reached }
      )
    },
    [applicationId, versionId, reachedScopeKey]
  )
  useEffect(() => {
    recordReachedPhase(reachedPhase)
  }, [reachedPhase, recordReachedPhase])
  useEffect(() => {
    // 确认门禁关闭后清除旧测试选择，避免最后一个产物完成时自动跳回测试视图。
    if (testEntryGate && !testEntryGate.allowed && manualOverride === 'test') {
      setPersistedWorkbenchPhase(applicationId, versionId, 'development')
      setOverrides((current) => ({ ...current, [applicationId]: 'development' }))
    }
  }, [applicationId, versionId, manualOverride, testEntryGate])

  const value = useMemo<WorkbenchPhaseContextValue>(() => {
    // 已发布版本只能回看其权威旅程位置，不能沿用迭代期间的手动查看阶段。
    const effectivePhase = locked ? derivedPhase : phase
    return {
      testEntryGate,
      phase: effectivePhase,
      derivedPhase,
      reachedPhase,
      recordReachedPhase,
      manualOverride,
      switchPhase: (next) => {
        if (locked) return
        if (next === 'test' && testEntryGate?.allowed !== true) return
        // 先保留当前最远阶段，再切换视图，避免运行刚结束或快速回退时丢失到达事实。
        recordReachedPhase(furthestWorkbenchPhase(reachedPhase, next ?? phase))
        // 只持久化用户明确的界面覆盖；传 null 表示恢复生命周期自动阶段。
        setPersistedWorkbenchPhase(applicationId, versionId, next)
        setOverrides((current) => ({ ...current, [applicationId]: next ?? null }))
      },
      agent: WORKBENCH_PHASE_AGENTS[effectivePhase],
      canEdit: (objectType) => isObjectEditableInPhase(objectType, effectivePhase),
      locked
    }
  }, [
    applicationId,
    versionId,
    manualOverride,
    derivedPhase,
    testEntryGate,
    phase,
    reachedPhase,
    recordReachedPhase,
    locked
  ])

  return <WorkbenchPhaseContext.Provider value={value}>{children}</WorkbenchPhaseContext.Provider>
}

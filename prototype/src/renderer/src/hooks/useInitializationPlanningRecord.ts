import { useEffect, useState } from 'react'
import type { ApplicationConfig } from '../typings'
import {
  createInitializationPlanningRecord,
  ensureInitializationPlanningRecord,
  readInitializationPlanningRecord,
  subscribeInitializationPlanning,
  type InitializationPlanningRecord,
  type InitializationPlanningSeed
} from '../initializationPlanning'

/** 只订阅当前应用版本的产物事实；所有修改由 AG-UI 规划动作执行。 */
export function useInitializationPlanningRecord(
  application: ApplicationConfig,
  seed: InitializationPlanningSeed
): { record: InitializationPlanningRecord } {
  // 首次渲染只读取或创建内存记录，持久化放到 effect，避免 render 阶段派发 storage 事件触发父组件更新。
  const [record, setRecord] = useState(
    () =>
      readInitializationPlanningRecord(application) ||
      createInitializationPlanningRecord(application, seed)
  )
  useEffect(() => {
    setRecord(ensureInitializationPlanningRecord(application, seed))
    return subscribeInitializationPlanning(() => {
      const stored = readInitializationPlanningRecord(application)
      if (stored) setRecord(stored)
    })
  }, [application.id, application.currentVersionId, seed])
  const current =
    record.applicationId === application.id &&
    record.versionId === (application.currentVersionId || 'current')
      ? record
      : readInitializationPlanningRecord(application) ||
        createInitializationPlanningRecord(application, seed)
  return { record: current }
}

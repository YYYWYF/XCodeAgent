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
  seed: InitializationPlanningSeed,
  options?: { readOnly?: boolean }
): { record: InitializationPlanningRecord } {
  // 首次渲染只读取或创建内存记录，持久化放到 effect，避免 render 阶段派发 storage 事件触发父组件更新。
  const [record, setRecord] = useState(
    () =>
      readInitializationPlanningRecord(application) ||
      createInitializationPlanningRecord(application, seed)
  )
  useEffect(() => {
    // 只读版本视图（已生成版本回看）不落盘：ensure 会为缺失记录的版本写 localStorage，
    // 违反"只读不写"；返回内存态记录即可支撑右侧审阅展示。
    setRecord(
      options?.readOnly
        ? readInitializationPlanningRecord(application) ||
            createInitializationPlanningRecord(application, seed)
        : ensureInitializationPlanningRecord(application, seed)
    )
    return subscribeInitializationPlanning(() => {
      const stored = readInitializationPlanningRecord(application)
      if (stored) setRecord(stored)
    })
  }, [application.id, application.currentVersionId, options?.readOnly, seed])
  const current =
    record.applicationId === application.id &&
    record.versionId === (application.currentVersionId || 'current')
      ? record
      : readInitializationPlanningRecord(application) ||
        createInitializationPlanningRecord(application, seed)
  return { record: current }
}

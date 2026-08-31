import { useEffect } from 'react'
import { useSyncExternalStore } from 'react'
import {
  getBackgroundTasks,
  subscribeBackgroundTasks
} from '../backgroundTasks'
import type { BackgroundTask } from '../backgroundTasks'
import { ensureBackgroundTaskEngine } from '../mock/backgroundTaskEngine'

/** 订阅统一后台任务流水的 React 入口；组件按应用与版本自行过滤。 */
export function useBackgroundTasks(): BackgroundTask[] {
  // 挂载即确保后台引擎在运行：刷新恢复后，中断在运行态的任务可以按剩余时间继续推进。
  useEffect(() => {
    ensureBackgroundTaskEngine()
  }, [])
  return useSyncExternalStore(subscribeBackgroundTasks, getBackgroundTasks, getBackgroundTasks)
}

/** 过滤指定应用与版本的后台任务，保持派发次序。 */
export function backgroundTasksForVersion(
  tasks: BackgroundTask[],
  applicationId: string,
  versionId: string
): BackgroundTask[] {
  return tasks
    .filter(
      (task) =>
        task.applicationId === applicationId && task.versionId === versionId
    )
    .sort((left, right) => left.createdAt - right.createdAt)
}

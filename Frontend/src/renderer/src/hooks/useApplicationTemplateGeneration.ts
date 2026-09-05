import { useCallback, useRef, useState } from 'react'
import { message } from 'antd'
import { saveApplication } from '../components/Welcome/applicationService'
import {
  activePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import {
  APPLICATION_TEMPLATE_GENERATION_ENABLED,
  ensureApplicationTemplateReadiness,
  retryApplicationTemplateReadiness
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

type PlanningUpdater = (
  updater: (current: PersistedActivePlanning[]) => PersistedActivePlanning[]
) => void

type UseApplicationTemplateGenerationOptions = {
  commitPlannings: PlanningUpdater
  hidePlanning: (applicationId: string) => void
  getVisiblePlanningId: () => string | undefined
  /** 将 Bootstrap 的权威终态同步给工作台，避免规划会话与工作台显示不同步。 */
  onLifecycleResolved: (applicationId: string, lifecycle: ApplicationLifecycle) => void
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ApplicationTemplateGenerationController = {
  generateApplicationTemplateFiles: (planning: PersistedActivePlanning) => Promise<boolean>
  /** 重试后端已明确失败的应用模板生成。 */
  retryApplicationTemplateFiles: (planning: PersistedActivePlanning) => Promise<boolean>
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingAppIds: ReadonlySet<string>
}

// 以应用 ID 隔离模板生成任务、生命周期提交和完成后的导航。
export function useApplicationTemplateGeneration({
  commitPlannings,
  hidePlanning,
  getVisiblePlanningId,
  onLifecycleResolved,
  onOpenWorkbench
}: UseApplicationTemplateGenerationOptions): ApplicationTemplateGenerationController {
  const tasksRef = useRef(new Map<string, Promise<boolean>>())
  const [generatingAppIds, setGeneratingAppIds] = useState<ReadonlySet<string>>(() => new Set())

  // 为单个应用生成模板文件，并复用同一应用尚未结束的幂等任务。
  const runApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning, retry: boolean): Promise<boolean> => {
      // 临时关闭模板生成时直接完成规划回调，不触发下载、初始化或生命周期结果提交。
      if (!APPLICATION_TEMPLATE_GENERATION_ENABLED) return Promise.resolve(true)

      const applicationId = planning.application.id
      const runningTask = tasksRef.current.get(applicationId)
      if (runningTask) return runningTask

      const task = (async (): Promise<boolean> => {
        setGeneratingAppIds((current) => new Set(current).add(applicationId))
        try {
          const lifecycle = retry
            ? await retryApplicationTemplateReadiness(planning.application, planning.threadId)
            : await ensureApplicationTemplateReadiness(planning.application, planning.threadId)
          const confirmedApplication = {
            ...planning.application,
            planningThreadId: planning.threadId
          }
          const persistedApplication = await saveApplication(confirmedApplication)
          const shouldOpenWorkbench = getVisiblePlanningId() === applicationId
          commitPlannings((current) =>
            current.map((currentPlanning) =>
              currentPlanning.application.id === applicationId
                ? {
                    ...currentPlanning,
                    application: persistedApplication,
                    lifecycle,
                    status: activePlanningStatus(lifecycle)
                  }
                : currentPlanning
            )
          )
          // 成功和失败都必须以同一份权威 lifecycle 驱动工作台卡片。
          onLifecycleResolved(applicationId, lifecycle)
          hidePlanning(applicationId)
          await onOpenWorkbench(persistedApplication, lifecycle)
          message.success(
            shouldOpenWorkbench
              ? '应用模板初始化完成，正在进入工作台'
              : `「${planning.application.appName}」初始化完成，可从最近项目打开`
          )
          return true
        } catch (reason) {
          console.error('[应用模板初始化失败]', reason)
          try {
            const lifecycle = await getApplicationLifecycle(planning.application)
            commitPlannings((current) =>
              current.map((currentPlanning) =>
                currentPlanning.application.id === applicationId
                  ? {
                      ...currentPlanning,
                      lifecycle,
                      status: activePlanningStatus(lifecycle)
                    }
                  : currentPlanning
              )
            )
            // Bootstrap 的失败事件不在 planning SSE 内，需主动校准工作台 store。
            onLifecycleResolved(applicationId, lifecycle)
          } catch (lifecycleError) {
            console.warn('[模板初始化失败后读取生命周期失败]', lifecycleError)
          }
          message.error(reason instanceof Error ? reason.message : String(reason))
          return false
        }
      })()

      tasksRef.current.set(applicationId, task)
      void task.then(
        () => {
          if (tasksRef.current.get(applicationId) === task) {
            tasksRef.current.delete(applicationId)
          }
          setGeneratingAppIds((current) => {
            if (!current.has(applicationId)) return current
            const next = new Set(current)
            next.delete(applicationId)
            return next
          })
        },
        () => {
          if (tasksRef.current.get(applicationId) === task) {
            tasksRef.current.delete(applicationId)
          }
          setGeneratingAppIds((current) => {
            if (!current.has(applicationId)) return current
            const next = new Set(current)
            next.delete(applicationId)
            return next
          })
        }
      )
      return task
    },
    [commitPlannings, hidePlanning, getVisiblePlanningId, onLifecycleResolved, onOpenWorkbench]
  )

  // 首次确认 TechnicalPlan 后启动模板初始化。
  const generateApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning): Promise<boolean> =>
      runApplicationTemplateFiles(planning, false),
    [runApplicationTemplateFiles]
  )

  // 失败后仅通过后端受控动作启动新的模板初始化轮次。
  const retryApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning): Promise<boolean> =>
      runApplicationTemplateFiles(planning, true),
    [runApplicationTemplateFiles]
  )

  return { generateApplicationTemplateFiles, generatingAppIds, retryApplicationTemplateFiles }
}

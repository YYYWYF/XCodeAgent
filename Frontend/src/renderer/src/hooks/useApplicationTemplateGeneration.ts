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
  ensureApplicationTemplateReadiness
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

type PlanningUpdater = (
  updater: (current: PersistedActivePlanning[]) => PersistedActivePlanning[]
) => void

type UseApplicationTemplateGenerationOptions = {
  commitPlannings: PlanningUpdater
  hidePlanning: (applicationId: string) => void
  getVisiblePlanningId: () => string | undefined
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ApplicationTemplateGenerationController = {
  generateApplicationTemplateFiles: (planning: PersistedActivePlanning) => Promise<boolean>
  waitForTemplateGeneration: (applicationId: string) => Promise<boolean> | undefined
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingAppIds: ReadonlySet<string>
}

// 以应用 ID 隔离模板生成任务、生命周期提交和完成后的导航。
export function useApplicationTemplateGeneration({
  commitPlannings,
  hidePlanning,
  getVisiblePlanningId,
  onOpenWorkbench
}: UseApplicationTemplateGenerationOptions): ApplicationTemplateGenerationController {
  const tasksRef = useRef(new Map<string, Promise<boolean>>())
  const [generatingAppIds, setGeneratingAppIds] = useState<ReadonlySet<string>>(() => new Set())

  // 为单个应用生成模板文件，并复用同一应用尚未结束的幂等任务。
  const generateApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning): Promise<boolean> => {
      // 临时关闭模板生成时直接完成规划回调，不触发下载、初始化或生命周期结果提交。
      if (!APPLICATION_TEMPLATE_GENERATION_ENABLED) return Promise.resolve(true)

      const applicationId = planning.application.id
      const runningTask = tasksRef.current.get(applicationId)
      if (runningTask) return runningTask

      const task = (async (): Promise<boolean> => {
        setGeneratingAppIds((current) => new Set(current).add(applicationId))
        try {
          const lifecycle = await ensureApplicationTemplateReadiness(
            planning.application,
            planning.threadId
          )
          const confirmedApplication = {
            ...planning.application,
            planningConfirmedAt: Date.now(),
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
    [commitPlannings, hidePlanning, getVisiblePlanningId, onOpenWorkbench]
  )

  // 返回指定应用正在执行的模板任务，供删除流程只等待该应用。
  const waitForTemplateGeneration = useCallback(
    (applicationId: string): Promise<boolean> | undefined => tasksRef.current.get(applicationId),
    []
  )

  return { generateApplicationTemplateFiles, waitForTemplateGeneration, generatingAppIds }
}

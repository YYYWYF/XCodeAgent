import { useCallback, useRef, useState } from 'react'
import { message } from 'antd'
import { saveApplication } from '../components/Welcome/applicationService'
import type {
  ApplicationPlanningCurrentEvent,
  ApplicationPlanningCurrentState
} from '../service/activeApplicationPlanning'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import {
  APPLICATION_TEMPLATE_GENERATION_ENABLED,
  ensureApplicationTemplateReadiness
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

type UseApplicationTemplateGenerationOptions = {
  dispatchPlanningEvent: (event: ApplicationPlanningCurrentEvent) => void
  hidePlanning: (applicationId: string) => void
  getVisiblePlanningId: () => string | undefined
  onApplicationLifecycleChange?: (lifecycle: ApplicationLifecycle) => void
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ApplicationTemplateGenerationController = {
  generateApplicationTemplateFiles: (
    planning: ApplicationPlanningCurrentState
  ) => Promise<boolean>
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingAppIds: ReadonlySet<string>
}

// 以应用 ID 隔离模板生成任务、生命周期提交和完成后的导航。
export function useApplicationTemplateGeneration({
  dispatchPlanningEvent,
  hidePlanning,
  getVisiblePlanningId,
  onApplicationLifecycleChange,
  onOpenWorkbench
}: UseApplicationTemplateGenerationOptions): ApplicationTemplateGenerationController {
  const tasksRef = useRef(new Map<string, Promise<boolean>>())
  const [generatingAppIds, setGeneratingAppIds] = useState<ReadonlySet<string>>(() => new Set())

  // 为单个应用生成模板文件，并复用同一应用尚未结束的幂等任务。
  const generateApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState): Promise<boolean> => {
      // 临时关闭模板生成时直接完成规划回调，不触发下载、初始化或生命周期结果提交。
      if (!APPLICATION_TEMPLATE_GENERATION_ENABLED) return Promise.resolve(true)

      const applicationId = planning.application.id
      const runningTask = tasksRef.current.get(applicationId)
      if (runningTask) return runningTask

      const task = (async (): Promise<boolean> => {
        setGeneratingAppIds((current) => new Set(current).add(applicationId))
        // 新一轮模板任务开始时清掉上一轮临时错误，生命周期仍由后端权威快照驱动。
        dispatchPlanningEvent({
          type: 'clear_error',
          applicationId,
          threadId: planning.threadId
        })
        try {
          const lifecycle = await ensureApplicationTemplateReadiness(
            planning.application,
            planning.threadId
          )
          const confirmedApplication = {
            ...planning.application,
            planningThreadId: planning.threadId
          }
          const persistedApplication = await saveApplication(confirmedApplication)
          const shouldOpenWorkbench = getVisiblePlanningId() === applicationId
          dispatchPlanningEvent({
            type: 'application_received',
            applicationId,
            threadId: planning.threadId,
            application: persistedApplication
          })
          dispatchPlanningEvent({
            type: 'lifecycle_received',
            applicationId,
            threadId: planning.threadId,
            lifecycle
          })
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
          dispatchPlanningEvent({
            type: 'run_failed',
            applicationId,
            threadId: planning.threadId,
            error: reason instanceof Error ? reason.message : String(reason)
          })
          try {
            const lifecycle = await getApplicationLifecycle(planning.application)
            dispatchPlanningEvent({
              type: 'lifecycle_received',
              applicationId,
              threadId: planning.threadId,
              lifecycle
            })
            onApplicationLifecycleChange?.(lifecycle)
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
    [
      dispatchPlanningEvent,
      getVisiblePlanningId,
      hidePlanning,
      onApplicationLifecycleChange,
      onOpenWorkbench
    ]
  )

  return { generateApplicationTemplateFiles, generatingAppIds }
}

import { useCallback, useRef, useState } from 'react'
import { message } from 'antd'
import { saveApplication } from '../components/Welcome/applicationService'
import type {
  ApplicationPlanningCurrentEvent,
  ApplicationPlanningCurrentState
} from '../service/activeApplicationPlanning'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { isBackendTransportError } from '../service/backendRequestFailure'
import { applicationWorkspaceKey, sameApplicationWorkspace } from '../service/applicationWorkspace'
import {
  APPLICATION_TEMPLATE_GENERATION_ENABLED,
  ensureApplicationTemplateReadiness,
  retryApplicationTemplateReadiness
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

type UseApplicationTemplateGenerationOptions = {
  getPlanningState: (applicationId: string) => ApplicationPlanningCurrentState | undefined
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
  /** 仅重试后端已经标记失败的 Bootstrap，不重启规划 Graph。 */
  retryApplicationTemplateFiles: (
    planning: ApplicationPlanningCurrentState
  ) => Promise<boolean>
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingWorkspaceKeys: ReadonlySet<string>
}

// 以应用 ID 隔离模板生成任务、生命周期提交和完成后的导航。
export function useApplicationTemplateGeneration({
  getPlanningState,
  dispatchPlanningEvent,
  hidePlanning,
  getVisiblePlanningId,
  onApplicationLifecycleChange,
  onOpenWorkbench
}: UseApplicationTemplateGenerationOptions): ApplicationTemplateGenerationController {
  const tasksRef = useRef(new Map<string, Promise<boolean>>())
  const [generatingWorkspaceKeys, setGeneratingWorkspaceKeys] = useState<ReadonlySet<string>>(() => new Set())

  // 为单个应用生成模板文件，并复用同一应用尚未结束的幂等任务。
  const runApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState, retry: boolean): Promise<boolean> => {
      // 临时关闭模板生成时直接完成规划回调，不触发下载、初始化或生命周期结果提交。
      if (!APPLICATION_TEMPLATE_GENERATION_ENABLED) return Promise.resolve(true)

      const applicationId = planning.application.id
      const workspaceKey = applicationWorkspaceKey(planning.application)
      /** 旧目录任务可完成后端工作，但不能覆盖新目录的索引、提示和当前界面。 */
      const isCurrentWorkspace = (): boolean => sameApplicationWorkspace(planning.application, getPlanningState(applicationId)?.application)
      if (!isCurrentWorkspace()) return Promise.resolve(false)
      const runningTask = tasksRef.current.get(workspaceKey)
      if (runningTask) return runningTask

      const task = (async (): Promise<boolean> => {
        setGeneratingWorkspaceKeys((current) => new Set(current).add(workspaceKey))
        // 新一轮模板任务开始时清掉上一轮临时错误，生命周期仍由后端权威快照驱动。
        dispatchPlanningEvent({
          type: 'clear_error',
          applicationId,
          workspaceRoot: planning.application.workspaceRoot,
          threadId: planning.threadId
        })
        try {
          const bootstrap = retry
            ? await retryApplicationTemplateReadiness(planning.application, planning.threadId)
            : await ensureApplicationTemplateReadiness(planning.application, planning.threadId)
          const lifecycle = bootstrap.lifecycle
          if (!isCurrentWorkspace()) return false
          const confirmedApplication = {
            ...planning.application,
            planningThreadId: planning.threadId,
            // 把远端推送结果落盘：Bootstrap 的非致命收尾事实，留给工作台或其他诊断
            // 入口使用。当前不主动展示给用户，失败由「提交并推送」入口在需要时报告。
            ...(bootstrap.repositoryBranch
              ? {
                  repositoryBranch: {
                    ...bootstrap.repositoryBranch,
                    updatedAt: Date.now()
                  }
                }
              : {})
          }
          const persistedApplication = await saveApplication(confirmedApplication)
          if (!isCurrentWorkspace()) return false
          const shouldOpenWorkbench = getVisiblePlanningId() === applicationId
          dispatchPlanningEvent({
            type: 'application_received',
            applicationId,
            workspaceRoot: planning.application.workspaceRoot,
            threadId: planning.threadId,
            application: persistedApplication
          })
          dispatchPlanningEvent({
            type: 'lifecycle_received',
            applicationId,
            workspaceRoot: planning.application.workspaceRoot,
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
          // 远端分支推送是 Bootstrap 的非致命收尾，结果持久化到 application.json，
          // 不再用 toast 打扰用户——"未提交到远端"的提示已由卡片自行承载。
          return true
        } catch (reason) {
          if (!isCurrentWorkspace()) return false
          console.error('[应用模板初始化失败]', reason)
          let connectionError: string | undefined
          let syncError: string | undefined
          try {
            const lifecycle = await getApplicationLifecycle(planning.application)
            if (!isCurrentWorkspace()) return false
            dispatchPlanningEvent({
              type: 'lifecycle_received',
              applicationId,
              workspaceRoot: planning.application.workspaceRoot,
              threadId: planning.threadId,
              lifecycle
            })
            onApplicationLifecycleChange?.(lifecycle)
          } catch (lifecycleError) {
            console.warn('[模板初始化失败后读取生命周期失败]', lifecycleError)
            if (isBackendTransportError(lifecycleError)) connectionError = (lifecycleError as Error).message
            else syncError = lifecycleError instanceof Error ? lifecycleError.message : '无法同步模板初始化状态。'
          }
          dispatchPlanningEvent({
            type: 'template_generation_failed', applicationId, workspaceRoot: planning.application.workspaceRoot, threadId: planning.threadId,
            error: reason instanceof Error ? reason.message : String(reason), connectionError, syncError
          })
          message.error(reason instanceof Error ? reason.message : String(reason))
          return false
        }
      })()

      tasksRef.current.set(workspaceKey, task)
      void task.then(
        () => {
          if (tasksRef.current.get(workspaceKey) === task) {
            tasksRef.current.delete(workspaceKey)
          }
          setGeneratingWorkspaceKeys((current) => {
            if (!current.has(workspaceKey)) return current
            const next = new Set(current)
            next.delete(workspaceKey)
            return next
          })
        },
        () => {
          if (tasksRef.current.get(workspaceKey) === task) {
            tasksRef.current.delete(workspaceKey)
          }
          setGeneratingWorkspaceKeys((current) => {
            if (!current.has(workspaceKey)) return current
            const next = new Set(current)
            next.delete(workspaceKey)
            return next
          })
        }
      )
      return task
    },
    [
      dispatchPlanningEvent,
      getPlanningState,
      getVisiblePlanningId,
      hidePlanning,
      onApplicationLifecycleChange,
      onOpenWorkbench
    ]
  )

  /** TechnicalPlan 确认后首次触发受控 Bootstrap。 */
  const generateApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState): Promise<boolean> =>
      runApplicationTemplateFiles(planning, false),
    [runApplicationTemplateFiles]
  )

  /** 模板失败后只执行 Bootstrap retry，保持当前 Planning Runtime 与线程不变。 */
  const retryApplicationTemplateFiles = useCallback(
    async (planning: ApplicationPlanningCurrentState): Promise<boolean> => {
      // 先读取后端现场并校准孤儿 Bootstrap；真实运行、成功或未知状态不能重发生成动作。
      let lifecycle: ApplicationLifecycle
      try {
        lifecycle = await getApplicationLifecycle(planning.application)
      } catch (reason) {
        const error = reason instanceof Error ? reason.message : '无法同步模板初始化状态。'
        if (!sameApplicationWorkspace(planning.application, getPlanningState(planning.application.id)?.application)) return false
        dispatchPlanningEvent({ type: 'template_generation_failed', applicationId: planning.application.id, workspaceRoot: planning.application.workspaceRoot,
          threadId: planning.threadId, error, connectionError: isBackendTransportError(reason) ? error : undefined,
          syncError: isBackendTransportError(reason) ? undefined : error })
        return false
      }
      if (!sameApplicationWorkspace(planning.application, getPlanningState(planning.application.id)?.application)) return false
      dispatchPlanningEvent({ type: 'lifecycle_received', applicationId: planning.application.id, workspaceRoot: planning.application.workspaceRoot,
        threadId: planning.threadId, lifecycle })
      onApplicationLifecycleChange?.(lifecycle)
      if (lifecycle.initialization.stage !== 'application_template_generation_failed') return false
      return runApplicationTemplateFiles({ ...planning, lifecycle }, true)
    },
    [runApplicationTemplateFiles, dispatchPlanningEvent, getPlanningState, onApplicationLifecycleChange]
  )

  return { generateApplicationTemplateFiles, generatingWorkspaceKeys, retryApplicationTemplateFiles }
}

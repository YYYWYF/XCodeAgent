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
  ensureApplicationTemplateReadiness,
  retryApplicationTemplateReadiness
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { asMessageClause } from '../service/repositoryBranch'
import type { RepositoryBranchOutcome } from '../service/repositoryBranch'

/**
 * 反馈模板基线推成远端分支的结果。
 *
 * 推送成功是默认预期，不再弹提示打扰；未推送或失败时必须说明，避免用户误以为代码
 * 已经在远端仓库里。这些情况都不影响应用本身可用。
 */
function notifyRepositoryBranch(outcome?: RepositoryBranchOutcome): void {
  if (!outcome || outcome.status === 'pushed') return
  const branch = outcome.branchName || '所选版本'
  if (outcome.status === 'skipped') {
    message.warning(
      outcome.message || `远端已存在版本 ${branch}，本次没有覆盖它。`
    )
    return
  }
  // 后端消息是完整句子（自带句号），嵌进本句前先去掉句末标点，否则会出现「。。」。
  message.warning(
    `版本 ${branch} 未提交到远端：${asMessageClause(outcome.message, '原因未知')}。应用本身可以正常使用，稍后可重试。`
  )
}

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
  /** 仅重试后端已经标记失败的 Bootstrap，不重启规划 Graph。 */
  retryApplicationTemplateFiles: (
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
  const runApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState, retry: boolean): Promise<boolean> => {
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
          const bootstrap = retry
            ? await retryApplicationTemplateReadiness(planning.application, planning.threadId)
            : await ensureApplicationTemplateReadiness(planning.application, planning.threadId)
          const lifecycle = bootstrap.lifecycle
          const confirmedApplication = {
            ...planning.application,
            planningThreadId: planning.threadId,
            // 把远端推送结果一并落盘：工作台的「应用模板已就绪」卡据此如实说明代码
            // 有没有真的到远端（早先只读本地 git 事实，推送失败时仍显示"已自动提交"）。
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
          // 远端分支推送是 Bootstrap 的非致命收尾：成功不打扰，没推上去要说清楚，
          // 否则用户会以为代码已经在远端了。
          notifyRepositoryBranch(bootstrap.repositoryBranch)
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

  /** TechnicalPlan 确认后首次触发受控 Bootstrap。 */
  const generateApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState): Promise<boolean> =>
      runApplicationTemplateFiles(planning, false),
    [runApplicationTemplateFiles]
  )

  /** 模板失败后只执行 Bootstrap retry，保持当前 Planning Runtime 与线程不变。 */
  const retryApplicationTemplateFiles = useCallback(
    (planning: ApplicationPlanningCurrentState): Promise<boolean> =>
      runApplicationTemplateFiles(planning, true),
    [runApplicationTemplateFiles]
  )

  return { generateApplicationTemplateFiles, generatingAppIds, retryApplicationTemplateFiles }
}

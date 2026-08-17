import { useCallback, useRef, useState } from 'react'
import { message } from 'antd'
import { saveApplication } from '../components/Welcome/applicationService'
import {
  activePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import { completeApplicationTemplateGeneration } from '../service/applicationLifecycle'
import { canOpenApplicationWorkbench } from '../service/applicationStorage'
import {
  fetchTemplateCode,
  generateApplicationTemplateFiles as writeApplicationTemplateFiles
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'

type PlanningUpdater = (
  updater: (current: PersistedActivePlanning[]) => PersistedActivePlanning[]
) => void

type UseApplicationTemplateGenerationOptions = {
  commitPlannings: PlanningUpdater
  dismissPlanning: (applicationId: string) => void
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
  dismissPlanning,
  hidePlanning,
  getVisiblePlanningId,
  onOpenWorkbench
}: UseApplicationTemplateGenerationOptions): ApplicationTemplateGenerationController {
  const tasksRef = useRef(new Map<string, Promise<boolean>>())
  const [generatingAppIds, setGeneratingAppIds] = useState<ReadonlySet<string>>(() => new Set())

  // 为单个应用生成模板文件，并复用同一应用尚未结束的幂等任务。
  const generateApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning): Promise<boolean> => {
      const applicationId = planning.application.id
      const runningTask = tasksRef.current.get(applicationId)
      if (runningTask) return runningTask

      const task = (async (): Promise<boolean> => {
        let failureMessage = ''
        const projectPath =
          planning.application.workspaceRoot || planning.application.projectParentPath || ''

        setGeneratingAppIds((current) => new Set(current).add(applicationId))
        try {
          await fetchTemplateCode(planning.application.schema, projectPath)
        } catch (templateError) {
          console.error('[模板拉取失败]', templateError)
          message.warning(`「${planning.application.appName}」模板拉取失败，可稍后重试`)
        }

        try {
          const result = await writeApplicationTemplateFiles(
            planning.application.schema,
            projectPath,
            planning.workflow
          )
          if (result.written.length > 0) {
            message.success(
              `「${planning.application.appName}」已生成 ${result.written.length} 个应用模板文件`
            )
          }
        } catch (reason) {
          console.error('[应用模板文件生成失败]', reason)
          failureMessage = reason instanceof Error ? reason.message : String(reason)
        }

        const lifecycle = await completeApplicationTemplateGeneration(
          planning.application,
          planning.threadId,
          !failureMessage,
          failureMessage || undefined
        )
        if (!canOpenApplicationWorkbench(planning.application, lifecycle)) {
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
          message.error(
            lifecycle.error?.message || `「${planning.application.appName}」应用模板文件生成失败`
          )
          return false
        }

        const confirmedApplication = {
          ...planning.application,
          planningConfirmedAt: Date.now(),
          planningThreadId: planning.threadId
        }
        const persistedApplication = await saveApplication(confirmedApplication)
        const shouldOpenWorkbench = getVisiblePlanningId() === applicationId
        // 保留 planning 在 activePlannings 中（供设计阶段渲染需求文档/UI设计稿 tab），
        // 只更新 lifecycle 到 ready_for_workbench 并隐藏 Modal。
        // dismissPlanning 推迟到用户点"进入开发"或从历史重新打开时（AppEntryPage 已有逻辑）。
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
        hidePlanning(applicationId)
        // 模板生成成功后总是更新工作台 application（含 planningConfirmedAt），
        // 即使 Modal 不可见（设计阶段 visible=false）也要让 AiChatPanel 收到更新，
        // 触发"进入开发"gate。shouldOpenWorkbench 只决定是否弹 success 消息。
        await onOpenWorkbench(persistedApplication, lifecycle)
        if (shouldOpenWorkbench) {
          message.success('应用模板文件生成完成，正在进入工作台')
        } else {
          message.success(`「${planning.application.appName}」初始化完成，可从最近项目打开`)
        }
        return true
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
    [commitPlannings, dismissPlanning, hidePlanning, getVisiblePlanningId, onOpenWorkbench]
  )

  // 返回指定应用正在执行的模板任务，供删除流程只等待该应用。
  const waitForTemplateGeneration = useCallback(
    (applicationId: string): Promise<boolean> | undefined => tasksRef.current.get(applicationId),
    []
  )

  return { generateApplicationTemplateFiles, waitForTemplateGeneration, generatingAppIds }
}

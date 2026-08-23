import { useCallback, useEffect, useRef, useState } from 'react'
import { message, Modal } from 'antd'
import {
  loadActiveApplicationPlannings,
  workflowApplicationLifecycle,
  type ActivePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import {
  APPLICATIONS_CHANGED_EVENT,
  deleteStoredAgentDirectory,
  removeStoredApplication
} from '../service/applicationStorage'
import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { cx } from '../utils'
import { useApplicationTemplateGeneration } from './useApplicationTemplateGeneration'

type UseActiveApplicationPlanningsOptions = {
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ActiveApplicationPlanningsController = {
  activePlannings: PersistedActivePlanning[]
  deletingPlanningIds: ReadonlySet<string>
  dismissPlanning: (applicationId: string) => void
  onPlanningConfirmed: (applicationId: string) => Promise<boolean>
  registerStopHandler: (applicationId: string, handler?: () => Promise<void>) => void
  removePlanning: (applicationId: string) => void
  returnHome: () => void
  showPlanning: (applicationId: string) => void
  startPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  updatePlanningStatus: (applicationId: string, status: ActivePlanningStatus) => void
  updatePlanningWorkflow: (applicationId: string, workflow: WorkflowRunPayload) => void
  visiblePlanningId?: string
}

// 维护最多三个相互隔离的应用初始化会话及其后台模板生成任务。
export function useActiveApplicationPlannings({
  onOpenWorkbench
}: UseActiveApplicationPlanningsOptions): ActiveApplicationPlanningsController {
  const [activePlannings, setActivePlannings] = useState<PersistedActivePlanning[]>([])
  const [visiblePlanningId, setVisiblePlanningId] = useState<string>()
  const [deletingPlanningIds, setDeletingPlanningIds] = useState<Set<string>>(() => new Set())
  const activePlanningsRef = useRef<PersistedActivePlanning[]>([])
  const visiblePlanningIdRef = useRef<string>()
  const refreshIdRef = useRef(0)
  const stopHandlersRef = useRef(new Map<string, () => Promise<void>>())

  // 同步更新 React 状态和异步回调读取的最新规划引用。
  const commitPlannings = useCallback(
    (updater: (current: PersistedActivePlanning[]) => PersistedActivePlanning[]): void => {
      setActivePlannings((current) => {
        const next = updater(current)
        activePlanningsRef.current = next
        return next
      })
    },
    []
  )

  // 切换当前可见规划，不影响其余已挂载会话继续运行。
  const setVisiblePlanning = useCallback((applicationId?: string): void => {
    visiblePlanningIdRef.current = applicationId
    setVisiblePlanningId(applicationId)
  }, [])

  // 启动及应用索引变化时恢复全部未完成规划，忽略已被新建动作淘汰的旧读取结果。
  useEffect(() => {
    let disposed = false

    const refreshActivePlannings = async (): Promise<void> => {
      const currentRefreshId = ++refreshIdRef.current
      const recovered = await loadActiveApplicationPlannings()
      if (disposed || currentRefreshId !== refreshIdRef.current) return
      activePlanningsRef.current = recovered
      setActivePlannings(recovered)
    }

    const handleApplicationsChanged = (): void => {
      void refreshActivePlannings()
    }

    void refreshActivePlannings()
    window.addEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    return () => {
      disposed = true
      window.removeEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    }
  }, [])

  // 启动新的独立规划会话，并保留其他未完成会话。
  const startPlanning = useCallback(
    (application: ApplicationConfig, threadId: string, lifecycle: ApplicationLifecycle): void => {
      refreshIdRef.current += 1
      commitPlannings((current) => [
        {
          application,
          lifecycle,
          status: 'running',
          threadId
        },
        ...current.filter((planning) => planning.application.id !== application.id)
      ])
      setVisiblePlanning(application.id)
    },
    [commitPlannings, setVisiblePlanning]
  )

  // 更新指定应用的规划状态，禁止跨应用覆盖。
  const updatePlanningStatus = useCallback(
    (applicationId: string, status: ActivePlanningStatus): void => {
      commitPlannings((current) => {
        const target = current.find((planning) => planning.application.id === applicationId)
        if (!target || target.status === status) return current
        return current.map((planning) =>
          planning.application.id === applicationId ? { ...planning, status } : planning
        )
      })
    },
    [commitPlannings]
  )

  // 更新指定应用的 Workflow 与生命周期快照，禁止跨应用覆盖。
  const updatePlanningWorkflow = useCallback(
    (applicationId: string, workflow: WorkflowRunPayload): void => {
      commitPlannings((current) =>
        current.map((planning) => {
          if (planning.application.id !== applicationId) return planning
          return {
            ...planning,
            lifecycle: workflowApplicationLifecycle(workflow) || planning.lifecycle,
            workflow
          }
        })
      )
    },
    [commitPlannings]
  )

  // 按应用注册独立停止句柄，删除一个计划时不会停止其他流。
  const registerStopHandler = useCallback(
    (applicationId: string, handler?: () => Promise<void>): void => {
      if (handler) {
        stopHandlersRef.current.set(applicationId, handler)
      } else {
        stopHandlersRef.current.delete(applicationId)
      }
    },
    []
  )

  // 从活动集合移除已经完成或删除的单个计划。
  const dismissPlanning = useCallback(
    (applicationId: string): void => {
      commitPlannings((current) =>
        current.filter((planning) => planning.application.id !== applicationId)
      )
      if (visiblePlanningIdRef.current === applicationId) {
        setVisiblePlanning(undefined)
      }
    },
    [commitPlannings, setVisiblePlanning]
  )

  // 为模板生成回调提供当前可见应用标识，避免捕获过期渲染状态。
  const getVisiblePlanningId = useCallback(
    (): string | undefined => visiblePlanningIdRef.current,
    []
  )
  const { generateApplicationTemplateFiles, waitForTemplateGeneration } =
    useApplicationTemplateGeneration({
      commitPlannings,
      dismissPlanning,
      getVisiblePlanningId,
      onOpenWorkbench
    })

  // 打开指定规划；模板生成失败的计划直接按原有语义触发重试。
  const showPlanning = useCallback(
    (applicationId: string): void => {
      const planning = activePlanningsRef.current.find(
        (candidate) => candidate.application.id === applicationId
      )
      if (!planning) return
      if (planning.lifecycle.initialization.stage === 'application_template_generation_failed') {
        void generateApplicationTemplateFiles(planning)
        return
      }
      setVisiblePlanning(applicationId)
    },
    [generateApplicationTemplateFiles, setVisiblePlanning]
  )

  // 停止并删除指定初始化计划，不触碰其他应用的任务和文件。
  const deletePlanning = useCallback(
    async (planning: PersistedActivePlanning): Promise<void> => {
      const applicationId = planning.application.id
      const workspaceRoot = planning.application.workspaceRoot
      if (!workspaceRoot || deletingPlanningIds.has(applicationId)) return
      setDeletingPlanningIds((current) => new Set(current).add(applicationId))
      try {
        await stopHandlersRef.current.get(applicationId)?.()
        await waitForTemplateGeneration(applicationId)?.catch(() => undefined)
        await deleteStoredAgentDirectory(workspaceRoot)
        await removeStoredApplication(applicationId)
        dismissPlanning(applicationId)
        message.success(`「${planning.application.appName}」初始化计划和 .xcodeagent 目录已删除`)
      } catch (reason) {
        const errorMessage = reason instanceof Error ? reason.message : String(reason)
        message.error(`删除「${planning.application.appName}」初始化计划失败：${errorMessage}`)
      } finally {
        setDeletingPlanningIds((current) => {
          const next = new Set(current)
          next.delete(applicationId)
          return next
        })
      }
    },
    [deletingPlanningIds, dismissPlanning, waitForTemplateGeneration]
  )

  // 二次确认单个计划的停止和目录清理范围。
  const removePlanning = useCallback(
    (applicationId: string): void => {
      const planning = activePlanningsRef.current.find(
        (candidate) => candidate.application.id === applicationId
      )
      if (!planning || deletingPlanningIds.has(applicationId)) return
      Modal.confirm({
        title: `停止并删除「${planning.application.appName}」的初始化计划？`,
        content: '只会停止该应用的规划，并删除其 .xcodeagent 目录及规划文档。',
        okText: '确认',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => deletePlanning(planning),
        wrapClassName: cx('welcome-modal', 'theme-light')
      })
    },
    [deletePlanning, deletingPlanningIds]
  )

  // 后台恢复到模板生成阶段的每个应用都独立续跑，不抢占当前可见页面。
  useEffect(() => {
    for (const planning of activePlannings) {
      if (
        planning.application.id !== visiblePlanningId &&
        planning.lifecycle.initialization.stage === 'generating_application_template_files'
      ) {
        void generateApplicationTemplateFiles(planning)
      }
    }
  }, [activePlannings, generateApplicationTemplateFiles, visiblePlanningId])

  // 仅确认回调所属应用的模板任务，忽略其他会话的完成状态。
  const onPlanningConfirmed = useCallback(
    (applicationId: string): Promise<boolean> => {
      const planning = activePlanningsRef.current.find(
        (candidate) => candidate.application.id === applicationId
      )
      return planning ? generateApplicationTemplateFiles(planning) : Promise.resolve(false)
    },
    [generateApplicationTemplateFiles]
  )

  // 返回首页时只隐藏当前规划，所有已挂载会话继续运行。
  const returnHome = useCallback((): void => {
    setVisiblePlanning(undefined)
  }, [setVisiblePlanning])

  return {
    activePlannings,
    deletingPlanningIds,
    dismissPlanning,
    onPlanningConfirmed,
    registerStopHandler,
    removePlanning,
    returnHome,
    showPlanning,
    startPlanning,
    updatePlanningStatus,
    updatePlanningWorkflow,
    visiblePlanningId
  }
}

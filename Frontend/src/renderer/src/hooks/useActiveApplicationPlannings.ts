import { useCallback, useEffect, useRef, useState } from 'react'
import {
  activePlanningStatus,
  loadActiveApplicationPlannings,
  workflowApplicationLifecycle,
  type ActivePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import { APPLICATIONS_CHANGED_EVENT } from '../service/applicationStorage'
import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { useApplicationTemplateGeneration } from './useApplicationTemplateGeneration'

type UseActiveApplicationPlanningsOptions = {
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ActiveApplicationPlanningsController = {
  activePlannings: PersistedActivePlanning[]
  dismissPlanning: (applicationId: string) => void
  /** 只隐藏规划 Modal（清 visiblePlanningId），不删除 activePlannings 中的 planning。 */
  hidePlanning: (applicationId: string) => void
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingAppIds: ReadonlySet<string>
  onTechnicalPlanConfirmed: (applicationId: string) => Promise<boolean>
  prepareApplicationDeletion: (applicationId: string) => Promise<void>
  registerStopHandler: (applicationId: string, handler?: () => Promise<void>) => void
  returnHome: () => void
  showPlanning: (applicationId: string) => void
  startPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle,
    visible?: boolean,
    restoreArtifactsFromDisk?: boolean
  ) => void
  stopPlanning: (applicationId: string) => Promise<void>
  updatePlanningLifecycle: (applicationId: string, lifecycle: ApplicationLifecycle) => void
  updatePlanningError: (applicationId: string, error?: string) => void
  updatePlanningStatus: (applicationId: string, status: ActivePlanningStatus) => void
  updatePlanningWorkflow: (applicationId: string, workflow: WorkflowRunPayload) => void
  visiblePlanningId?: string
}

// 维护相互隔离的应用初始化会话及其后台模板生成任务。
export function useActiveApplicationPlannings({
  onOpenWorkbench
}: UseActiveApplicationPlanningsOptions): ActiveApplicationPlanningsController {
  const [activePlannings, setActivePlannings] = useState<PersistedActivePlanning[]>([])
  const [visiblePlanningId, setVisiblePlanningId] = useState<string>()
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
    (
      application: ApplicationConfig,
      threadId: string,
      lifecycle: ApplicationLifecycle,
      visible = true,
      restoreArtifactsFromDisk = false
    ): void => {
      refreshIdRef.current += 1
      commitPlannings((current) => [
        {
          application,
          lifecycle,
          restoreArtifactsFromDisk,
          status: 'running',
          threadId
        },
        ...current.filter((planning) => planning.application.id !== application.id)
      ])
      // visible=false 时只挂载规划会话（Modal 隐藏但继续跑 graph），用于新建应用后
      // 直接进工作台、规划在后台运行的场景；后续 awaiting_user 时由 AppEntryPage 自动弹出。
      if (visible) {
        setVisiblePlanning(application.id)
      }
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

  // 使用后端权威快照同步单个初始化计划，停止后不从旧 Workflow 状态推断下一阶段。
  const updatePlanningLifecycle = useCallback(
    (applicationId: string, lifecycle: ApplicationLifecycle): void => {
      commitPlannings((current) =>
        current.map((planning) =>
          planning.application.id === applicationId
            ? { ...planning, lifecycle, status: activePlanningStatus(lifecycle) }
            : planning
        )
      )
    },
    [commitPlannings]
  )

  // 将后台规划容器捕获到的模型错误同步到工作台，避免后台失败只剩空白占位。
  const updatePlanningError = useCallback(
    (applicationId: string, error?: string): void => {
      const normalizedError = error?.trim() || undefined
      commitPlannings((current) => {
        const target = current.find((planning) => planning.application.id === applicationId)
        if (!target || target.error === normalizedError) return current
        return current.map((planning) =>
          planning.application.id === applicationId
            ? { ...planning, error: normalizedError }
            : planning
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

  // 停止指定应用的主规划 Workflow，供工作台自由变更入口复用同一停止句柄。
  const stopPlanning = useCallback(async (applicationId: string): Promise<void> => {
    await stopHandlersRef.current.get(applicationId)?.()
  }, [])

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

  // 只隐藏指定规划的 Modal，保留 planning 在 activePlannings 中，
  // 供工作台设计阶段继续读取 planningWorkflow 渲染需求文档/UI设计稿 tab。
  const hidePlanning = useCallback(
    (applicationId: string): void => {
      if (visiblePlanningIdRef.current === applicationId) {
        setVisiblePlanning(undefined)
      }
    },
    [setVisiblePlanning]
  )

  // 为模板生成回调提供当前可见应用标识，避免捕获过期渲染状态。
  const getVisiblePlanningId = useCallback(
    (): string | undefined => visiblePlanningIdRef.current,
    []
  )
  const { generateApplicationTemplateFiles, generatingAppIds } =
    useApplicationTemplateGeneration({
      commitPlannings,
      hidePlanning,
      getVisiblePlanningId,
      onOpenWorkbench
    })

  // 显示指定应用已经挂载的规划容器，供工作台错误恢复入口使用。
  const showPlanning = useCallback(
    (applicationId: string): void => {
      if (
        !activePlanningsRef.current.some((planning) => planning.application.id === applicationId)
      ) {
        return
      }
      setVisiblePlanning(applicationId)
    },
    [setVisiblePlanning]
  )

  // 后端销毁准备完成后卸载该应用规划容器；实际运行停止由工作区级协议统一负责。
  const prepareApplicationDeletion = useCallback(
    async (applicationId: string): Promise<void> => {
      dismissPlanning(applicationId)
    },
    [dismissPlanning]
  )

  // 仅确认回调所属应用的模板任务，忽略其他会话的完成状态。
  const onTechnicalPlanConfirmed = useCallback(
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
    dismissPlanning,
    hidePlanning,
    generatingAppIds,
    onTechnicalPlanConfirmed,
    prepareApplicationDeletion,
    registerStopHandler,
    returnHome,
    showPlanning,
    startPlanning,
    stopPlanning,
    updatePlanningLifecycle,
    updatePlanningError,
    updatePlanningStatus,
    updatePlanningWorkflow,
    visiblePlanningId
  }
}

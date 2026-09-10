import { useCallback, useEffect, useRef, useState } from 'react'
import {
  loadActiveApplicationPlannings,
  reduceApplicationPlanningCurrentState,
  type ApplicationPlanningCurrentEvent,
  type ApplicationPlanningCurrentState
} from '../service/activeApplicationPlanning'
import { APPLICATIONS_CHANGED_EVENT } from '../service/applicationStorage'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { useApplicationTemplateGeneration } from './useApplicationTemplateGeneration'

type UseActiveApplicationPlanningsOptions = {
  onApplicationLifecycleChange?: (lifecycle: ApplicationLifecycle) => void
  onOpenWorkbench: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => Promise<void> | void
}

type ActiveApplicationPlanningsController = {
  activePlannings: ApplicationPlanningCurrentState[]
  dispatchPlanningEvent: (event: ApplicationPlanningCurrentEvent) => void
  dismissPlanning: (applicationId: string) => void
  getPlanningState: (applicationId: string) => ApplicationPlanningCurrentState | undefined
  /** 只隐藏规划 Modal（清 visiblePlanningId），不删除 activePlannings 中的 planning。 */
  hidePlanning: (applicationId: string) => void
  /** 当前正在生成模板的应用 ID 集合（驱动前端加载态卡片）。 */
  generatingAppIds: ReadonlySet<string>
  onTechnicalPlanConfirmed: (applicationId: string) => Promise<boolean>
  retryTemplateGeneration: (applicationId: string) => Promise<boolean>
  registerStopHandler: (applicationId: string, handler?: () => Promise<void>) => void
  returnHome: () => void
  showPlanning: (applicationId: string) => void
  startPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle,
    visible?: boolean,
    restoreArtifactsFromDisk?: boolean
  ) => ApplicationPlanningCurrentState
  stopPlanning: (applicationId: string) => Promise<void>
  visiblePlanningId?: string
}

// 维护相互隔离的应用初始化会话及其后台模板生成任务。
export function useActiveApplicationPlannings({
  onApplicationLifecycleChange,
  onOpenWorkbench
}: UseActiveApplicationPlanningsOptions): ActiveApplicationPlanningsController {
  const [activePlannings, setActivePlannings] = useState<ApplicationPlanningCurrentState[]>([])
  const [visiblePlanningId, setVisiblePlanningId] = useState<string>()
  const activePlanningsRef = useRef<ApplicationPlanningCurrentState[]>([])
  const visiblePlanningIdRef = useRef<string>()
  const refreshIdRef = useRef(0)
  const stopHandlersRef = useRef(new Map<string, () => Promise<void>>())

  // 同步更新 React 状态和异步回调读取的最新规划引用。
  const commitPlannings = useCallback(
    (
      updater: (
        current: ApplicationPlanningCurrentState[]
      ) => ApplicationPlanningCurrentState[]
    ): void => {
      const next = updater(activePlanningsRef.current)
      activePlanningsRef.current = next
      setActivePlannings(next)
    },
    []
  )

  // 直接读取同步权威引用，确保长期异步 Runtime 能立即看到刚提交的规划事件。
  const getPlanningState = useCallback(
    (applicationId: string): ApplicationPlanningCurrentState | undefined =>
      activePlanningsRef.current.find(
        (planning) => planning.application.id === applicationId
      ),
    []
  )

  // 切换当前可见规划，不影响其余已挂载会话继续运行。
  const setVisiblePlanning = useCallback((applicationId?: string): void => {
    visiblePlanningIdRef.current = applicationId
    setVisiblePlanningId(applicationId)
  }, [])

  // 启动及应用索引变化时补入未完成规划，既有实时状态只接受同线程的单调 lifecycle 更新。
  useEffect(() => {
    let disposed = false

    const refreshActivePlannings = async (): Promise<void> => {
      const currentRefreshId = ++refreshIdRef.current
      const recovered = await loadActiveApplicationPlannings()
      if (disposed || currentRefreshId !== refreshIdRef.current) return
      commitPlannings((current) => {
        const recoveredByApplication = new Map(
          recovered.map((planning) => [planning.application.id, planning])
        )
        const currentApplicationIds = new Set(current.map((planning) => planning.application.id))
        const retainedCurrent = current.map((existing) => {
          const incoming = recoveredByApplication.get(existing.application.id)
          // 冷恢复只能补充同线程的应用信息与更新 lifecycle，不能替换已存在的实时线程或 workflow。
          if (!incoming || existing.threadId !== incoming.threadId) return existing
          const withLifecycle = reduceApplicationPlanningCurrentState(existing, {
            type: 'lifecycle_received',
            applicationId: incoming.application.id,
            threadId: incoming.threadId,
            lifecycle: incoming.lifecycle
          })
          return reduceApplicationPlanningCurrentState(withLifecycle, {
            type: 'application_received',
            applicationId: incoming.application.id,
            threadId: incoming.threadId,
            application: incoming.application
          })
        })
        return [
          ...retainedCurrent,
          ...recovered.filter((planning) => !currentApplicationIds.has(planning.application.id))
        ]
      })
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
  }, [commitPlannings])

  // 启动新的独立规划会话，并保留其他未完成会话。
  const startPlanning = useCallback(
    (
      application: ApplicationConfig,
      threadId: string,
      lifecycle: ApplicationLifecycle,
      visible = true,
      restoreArtifactsFromDisk = false
    ): ApplicationPlanningCurrentState => {
      refreshIdRef.current += 1
      const planning: ApplicationPlanningCurrentState = {
        application,
        lifecycle,
        restoreArtifactsFromDisk,
        threadId,
        transportState: 'idle'
      }
      commitPlannings((current) => [
        planning,
        ...current.filter((planning) => planning.application.id !== application.id)
      ])
      // visible=false 时只挂载规划会话（Modal 隐藏但继续跑 graph），用于新建应用后
      // 直接进工作台、规划在后台运行的场景；后续 awaiting_user 时由 AppEntryPage 自动弹出。
      if (visible) {
        setVisiblePlanning(application.id)
      }
      return planning
    },
    [commitPlannings, setVisiblePlanning]
  )

  // 所有规划业务状态只经过纯 reducer 更新，禁止组件分别写 workflow/lifecycle/error/status。
  const dispatchPlanningEvent = useCallback(
    (event: ApplicationPlanningCurrentEvent): void => {
      commitPlannings((current) =>
        current.map((planning) =>
          planning.application.id === event.applicationId
            ? reduceApplicationPlanningCurrentState(planning, event)
            : planning
        )
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
      dispatchPlanningEvent,
      hidePlanning,
      getVisiblePlanningId,
      onApplicationLifecycleChange,
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

  // 按应用复用同一个模板生成入口，TechnicalPlan 确认和失败重试都不重启规划 Graph。
  const runTemplateGeneration = useCallback(
    (applicationId: string): Promise<boolean> => {
      const planning = activePlanningsRef.current.find(
        (candidate) => candidate.application.id === applicationId
      )
      return planning ? generateApplicationTemplateFiles(planning) : Promise.resolve(false)
    },
    [generateApplicationTemplateFiles]
  )

  const onTechnicalPlanConfirmed = runTemplateGeneration
  const retryTemplateGeneration = runTemplateGeneration

  // 返回首页时只隐藏当前规划，所有已挂载会话继续运行。
  const returnHome = useCallback((): void => {
    setVisiblePlanning(undefined)
  }, [setVisiblePlanning])

  return {
    activePlannings,
    dispatchPlanningEvent,
    dismissPlanning,
    getPlanningState,
    hidePlanning,
    generatingAppIds,
    onTechnicalPlanConfirmed,
    retryTemplateGeneration,
    registerStopHandler,
    returnHome,
    showPlanning,
    startPlanning,
    stopPlanning,
    visiblePlanningId
  }
}

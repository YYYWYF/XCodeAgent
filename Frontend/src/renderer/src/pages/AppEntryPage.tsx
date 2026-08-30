import { useCallback, useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import { saveApplication } from '../components/Welcome/applicationService'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { useActiveApplicationPlannings } from '../hooks/useActiveApplicationPlannings'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
import { useApplicationTheme } from '../hooks/useApplicationTheme'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type { WorkflowRevisionContinuationHandoff } from '../service/applicationPagePlanning'
import { stopProjectPreview } from '../service/projectLaunch'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'
import { hasApplicationEnteredDevelopment } from '../workbenchPhase'
import { cx } from '../utils'

type ActiveSurface = 'welcome' | 'workbench'

/** 判断恢复快照是否正停在设计到规划的显式入口门禁。 */
function isPlanningStageEntryWorkflow(workflow?: WorkflowRunPayload): boolean {
  if (!workflow) return false
  const clarificationCandidates = [
    workflow.summary?.clarification,
    workflow.result?.clarification,
    workflow.state?.clarification
  ]
  const mode = clarificationCandidates
    .map((value) =>
      value && typeof value === 'object'
        ? String((value as Record<string, unknown>).mode || '')
        : ''
    )
    .find(Boolean)
  return workflow.summary?.phase === 'planning_stage_entry' || mode === 'planning_stage_entry_confirmation'
}

/** 读取应用实际绑定的预览工作区，用于区分不同生成项目进程。 */
function applicationPreviewWorkspace(application: ApplicationConfig): string {
  return application.workspaceRoot || application.projectParentPath || ''
}

/** 在独立窗口恢复应用与 Graph checkpoint 前显示规划阶段专属首帧，避免闪回欢迎页。 */
function PlanningWindowBootScreen({ theme }: { theme: 'dark' | 'light' }): JSX.Element {
  return (
    <div className={cx('workbench-shell')} data-theme={theme}>
      <div aria-live="polite" className={cx('workbench-entry')} role="status">
        <div className={cx('workbench-entry-glow', 'glow-one')} />
        <div className={cx('workbench-entry-glow', 'glow-two')} />
        <div className={cx('workbench-entry-content')}>
          <div className={cx('workbench-entry-mark')} aria-hidden="true">
            <span />
            <span />
          </div>
          <div className={cx('workbench-entry-kicker')}>PLANNING AGENT</div>
          <h1>正在进入规划阶段</h1>
          <p>正在恢复规划会话与确认上下文</p>
          <div className={cx('workbench-entry-progress')} aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>
      </div>
    </div>
  )
}

/** 在应用根部持有不会随工作台显隐而销毁的会话运行管理器。 */
export default function AppEntryPage(): JSX.Element {
  return (
    <SessionRuntimeProvider>
      <AppEntryContent />
    </SessionRuntimeProvider>
  )
}

// 在欢迎页、多个全屏规划会话与应用工作台之间维护顶层导航。
function AppEntryContent(): JSX.Element {
  const launchContext = window.xcodeAgent?.launchContext
  const { theme, setTheme } = useApplicationTheme()
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activeSurface, setActiveSurface] = useState<ActiveSurface>('welcome')
  const activePreviewWorkspaceRef = useRef('')
  const { lifecycle: applicationLifecycle, mergeLifecycle: mergeApplicationLifecycle } =
    useApplicationLifecycleStore(activeApplication?.id || '')

  // 规划确认提交句柄：由 ApplicationPagePlanningModal 按 applicationId 注册，供工作台中间区调用。
  // 多个工作区并行挂载时，按 applicationId 路由提交，避免单一 ref 被最后挂载的 Modal 覆盖串用。
  // 设计阶段不弹 Modal，确认卡内嵌工作台，提交时通过此 ref 驱动规划 graph 继续。
  const planningSubmitByAppRef = useRef<
    Record<
      string,
      | ((
          workflow: WorkflowRunPayload,
          answers: WorkflowClarificationAnswers,
          editedRequirementSpec?: Record<string, unknown>,
          requirementSpecFeedback?: string,
          designChangeRequest?: string
        ) => Promise<void>)
      | undefined
    >
  >({})
  // 工作台确认卡可能先于隐藏的规划 Modal 完成挂载；句柄暂未注册时先缓存提交，
  // 避免用户点击后被静默丢弃，等 Modal 注册后立即转发本次提交。
  const pendingPlanningSubmitByAppRef = useRef<
    Record<
      string,
      | {
          workflow: WorkflowRunPayload
          answers: WorkflowClarificationAnswers
          editedRequirementSpec?: Record<string, unknown>
          requirementSpecFeedback?: string
          designChangeRequest?: string
          resolve: () => void
          reject: (reason?: unknown) => void
        }
      | undefined
    >
  >({})
  const [planningSubmitRevision, setPlanningSubmitRevision] = useState(0)
  const planningLaunchOpenedRef = useRef(false)
  const planningLaunchSubmittedRef = useRef(false)

  // 规划重试句柄：由 Modal 通过 onRetryHandlerChange 注册，
  // 聊天区域错误卡片的重试按钮直接调用它，不弹出全屏 Modal。
  const planningRetryByAppRef = useRef<Record<string, (() => void) | undefined>>({})

  // design revision 起始句柄由原 planning Modal 注册；已进入开发的应用会先重新挂载
  // 该 Modal，再消费排队动作，避免另建临时 planning session。
  const planningDesignRevisionByAppRef = useRef<
    Record<string, ((input: WorkflowDesignStageRevisionStart) => Promise<void>) | undefined>
  >({})
  const pendingDesignRevisionByAppRef = useRef<
    Record<
      string,
      | {
          input: WorkflowDesignStageRevisionStart
          reject: (reason?: unknown) => void
          resolve: () => void
          timer: number
        }
      | undefined
    >
  >({})
  const revisionContinuationByAppRef = useRef<
    Record<string, ((handoff: WorkflowRevisionContinuationHandoff) => Promise<void>) | undefined>
  >({})
  const pendingRevisionContinuationByAppRef = useRef<
    Record<
      string,
      | {
          handoff: WorkflowRevisionContinuationHandoff
          promise: Promise<void>
          reject: (reason?: unknown) => void
          resolve: () => void
        }
      | undefined
    >
  >({})

  /** 将规划 Graph 签发的 continuation 转交给工作台；句柄尚未挂载时保留同一次交接。 */
  const dispatchRevisionContinuation = useCallback(
    (
      applicationId: string,
      handoff: WorkflowRevisionContinuationHandoff
    ): Promise<void> => {
      const handler = revisionContinuationByAppRef.current[applicationId]
      if (handler) return handler(handoff)

      const pending = pendingRevisionContinuationByAppRef.current[applicationId]
      if (
        pending?.handoff.continuation.changeId === handoff.continuation.changeId &&
        pending.handoff.continuation.token === handoff.continuation.token
      ) {
        return pending.promise
      }
      pending?.reject(new Error('revision continuation 已被更新的服务端状态替代。'))

      let resolvePending!: () => void
      let rejectPending!: (reason?: unknown) => void
      const promise = new Promise<void>((resolve, reject) => {
        resolvePending = resolve
        rejectPending = reject
      })
      pendingRevisionContinuationByAppRef.current[applicationId] = {
        handoff,
        promise,
        reject: rejectPending,
        resolve: resolvePending
      }
      return promise
    },
    []
  )

  // 规划流式数据注入句柄：由工作台 AiChatPanel 注册，Modal 转发 onContent/onWorkflow 时调用，
  // 把规划流式内容注入工作台 MessageList（设计阶段产品 Agent 对话 + 工作流卡片）。
  const planningStreamRef = useRef<
    | ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void)
    | null
  >(null)
  // 当前活动工作台的规划线程标识：只有匹配该 threadId 的规划流式才注入工作台，
  // 避免后台其他应用规划的流式 chunk 串入当前工作台对话。
  const activePlanningThreadIdRef = useRef<string | undefined>(undefined)
  // 注入句柄注册前缓存的流式 chunk（带 threadId），注册后按当前工作台 threadId 回放，
  // 避免后台其他应用规划的 chunk 串入当前工作台对话。
  const pendingPlanningChunksRef = useRef<
    Array<{ threadId: string; chunk: { content?: string; workflow?: WorkflowRunPayload } }>
  >([])
  const deliverPlanningChunk = useCallback(
    (threadId: string, chunk: { content?: string; workflow?: WorkflowRunPayload }) => {
      // 只注入当前活动工作台对应线程的流式，丢弃其他后台规划的 chunk。
      // 但当前工作台 threadId 尚未确定（ref=undefined，新建应用首次进入）时不丢弃，
      // 缓存待 ref 就绪后回放——避免最早的 workflow 快照丢失导致一直 loading。
      const activeThreadId = activePlanningThreadIdRef.current
      if (activeThreadId !== undefined && activeThreadId !== threadId) return
      const stream = planningStreamRef.current
      if (stream) {
        stream(chunk)
      } else {
        pendingPlanningChunksRef.current.push({ threadId, chunk })
      }
    },
    []
  )

  // 工作台注册规划流式注入句柄；稳定化避免 AiChatPanel 注入 effect 反复触发。
  const handlePlanningStreamReady = useCallback(
    (inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null) => {
      planningStreamRef.current = inject
      const activeThreadId = activePlanningThreadIdRef.current
      // 注册后回放工作台挂载前缓存的 chunk，只回放当前工作台 threadId 的。
      // activeThreadId 尚未确定时不回放，由 activePlanningThreadId effect 在 threadId 就绪后回放。
      if (inject && activeThreadId) {
        const pending = pendingPlanningChunksRef.current
        if (pending.length) {
          const matched = pending.filter((item) => item.threadId === activeThreadId)
          pendingPlanningChunksRef.current = []
          for (const item of matched) {
            inject(item.chunk)
          }
        }
      }
    },
    []
  )

  // 切换到另一个应用工作区前停止上一个应用的生成项目预览。
  const stopPreviousPreviewIfNeeded = useCallback(async (nextApplication: ApplicationConfig) => {
    const previousWorkspace = activePreviewWorkspaceRef.current
    const nextWorkspace = applicationPreviewWorkspace(nextApplication)
    if (!previousWorkspace || previousWorkspace === nextWorkspace) return
    activePreviewWorkspaceRef.current = ''
    try {
      const result = await stopProjectPreview(previousWorkspace)
      if (result.status === 'failed') {
        console.warn('停止上一个应用预览失败。', result)
      } else {
        void window.xcodeAgent?.projectPreview?.unregisterWorkspace({
          workspaceRoot: previousWorkspace
        })
      }
    } catch (error) {
      console.warn('停止上一个应用预览失败。', error)
    }
  }, [])

  // 打开指定应用工作台，并校准该应用自己的生命周期。
  const openWorkbench = useCallback(
    async (application: ApplicationConfig, lifecycle?: ApplicationLifecycle): Promise<void> => {
      await stopPreviousPreviewIfNeeded(application)
      setActiveApplication(application)
      activePreviewWorkspaceRef.current = applicationPreviewWorkspace(application)
      if (lifecycle) mergeApplicationLifecycle(lifecycle)
      setActiveSurface('workbench')
    },
    [mergeApplicationLifecycle, stopPreviousPreviewIfNeeded]
  )

  const planningController = useActiveApplicationPlannings({
    onOpenWorkbench: openWorkbench,
    theme
  })

  // 设计阶段二次修改始终恢复应用创建时的 planning Graph；若开发阶段已卸载规划
  // Modal，则先用持久化 planningThreadId 后台挂载，等其注册句柄后再开始本轮请求。
  const handleStartDesignStageRevision = useCallback(
    async (
      application: ApplicationConfig,
      input: WorkflowDesignStageRevisionStart
    ): Promise<void> => {
      const registered = planningDesignRevisionByAppRef.current[application.id]
      if (registered) {
        await registered(input)
        return
      }
      const lifecycle = await getApplicationLifecycle(application)
      const threadId = application.planningThreadId || lifecycle.initialization.threadId
      if (!threadId) throw new Error('当前应用缺少原 planning thread，无法返回设计阶段。')
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          delete pendingDesignRevisionByAppRef.current[application.id]
          reject(new Error('原 planning 会话挂载超时，请重新打开应用后重试。'))
        }, 5000)
        pendingDesignRevisionByAppRef.current[application.id] = {
          input,
          reject,
          resolve,
          timer
        }
        planningController.startPlanning(application, threadId, lifecycle, false)
      })
    },
    [planningController]
  )

  // 同步当前活动工作台的规划线程标识到 ref，供 deliverPlanningChunk 按 threadId 过滤。
  // 只有匹配该 threadId 的规划流式才注入工作台，避免后台其他应用规划串入对话。
  const activePlanning = activeApplication
    ? planningController.activePlannings.find(
        (item) => item.application.id === activeApplication.id
      )
    : undefined
  const activePlanningThreadId = activePlanning?.threadId
  const templateGenerationFailed =
    activePlanning?.lifecycle.initialization.stage === 'application_template_generation_failed'

  // 独立规划窗口从持久化活动规划中定位应用，首帧直接进入对应工作台。
  useEffect(() => {
    if (!launchContext || planningLaunchOpenedRef.current) return
    const planning = planningController.activePlannings.find(
      (item) =>
        item.application.id === launchContext.applicationId &&
        item.threadId === launchContext.graphThreadId
    )
    if (!planning) return
    planningLaunchOpenedRef.current = true
    void openWorkbench(planning.application, planning.lifecycle)
  }, [launchContext, openWorkbench, planningController.activePlannings])

  // 新窗口只在 checkpoint 恢复出入口门禁后提交一次，避免原窗口和新窗口并发 resume。
  useEffect(() => {
    if (!launchContext || planningLaunchSubmittedRef.current) return
    const workflow = activePlanning?.workflow
    const submit = planningSubmitByAppRef.current[launchContext.applicationId]
    if (!workflow || !submit || !isPlanningStageEntryWorkflow(workflow)) return
    planningLaunchSubmittedRef.current = true
    void submit(workflow, {
      planning_stage_entry: 'enter',
      __applicationPlanningAction: 'enter_planning'
    }).catch((error) => console.warn('规划阶段入口提交失败。', error))
  }, [activePlanning?.workflow, launchContext, planningSubmitRevision])

  useEffect(() => {
    activePlanningThreadIdRef.current = activePlanningThreadId
    // threadId 就绪后，如果 stream 已注册且有待回放的缓存，按 threadId 回放。
    // 解决新建应用首次进入时 ref=undefined 导致 handlePlanningStreamReady 过滤掉所有缓存的问题。
    if (activePlanningThreadId && planningStreamRef.current) {
      const pending = pendingPlanningChunksRef.current
      if (pending.length) {
        const matched = pending.filter((item) => item.threadId === activePlanningThreadId)
        pendingPlanningChunksRef.current = []
        for (const item of matched) {
          planningStreamRef.current(item.chunk)
        }
      }
    }
  }, [activePlanningThreadId])

  // 新建应用：注册规划会话（Modal 隐藏但挂载，graph 继续跑），直接进工作台。
  // 需求确认等 awaiting_user 阶段由下方 effect 自动弹 Modal 覆盖在工作台上。
  const handleOpenWorkbenchAfterCreate = useCallback(
    (application: ApplicationConfig, threadId: string, lifecycle: ApplicationLifecycle): void => {
      planningController.startPlanning(application, threadId, lifecycle, false)
      void openWorkbench(application, lifecycle)
    },
    [openWorkbench, planningController]
  )

  // 从工作台直接返回欢迎页，后台任务由保持挂载的工作台和规划页继续运行。
  const handleReturnWelcome = (): void => {
    setActiveSurface('welcome')
  }

  // 设计阶段（product）的规划确认（需求确认/UI确认/项目规划确认）直接在工作台
  // 中间区完成，不弹"生成应用规划"全屏 Modal。Modal 仍挂载跑规划 graph，但 visible=false。
  // 提交确认时由工作台中间区的 ApplicationPlanningQuestionPanel 通过 planningSubmitByAppRef 调用。

  // 已完成应用直接进入工作台；未完成应用只打开其对应的独立规划会话。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      // 用户是否已确认进入开发（持久化标志，跨会话保留）。模板生成完但用户还没点
      // "进入开发"按钮时此标志为空，应进设计阶段回显历史卡片，而非直接进开发。
      const enterDevConfirmed = hasApplicationEnteredDevelopment(application.id)
      if (application.source !== 'new') {
        // 已有工作区不属于新建应用模板生命周期，保持原有直接打开语义。
        await openWorkbench(application)
        return
      }
      try {
        const lifecycle = await getApplicationLifecycle(application)
        const readyForWorkbench =
          lifecycle?.initialization?.stage === 'ready_for_workbench'
        if (readyForWorkbench && enterDevConfirmed) {
          // lifecycle 已就绪且用户已确认进入开发：进开发阶段。
          const confirmedApplication = application.planningConfirmedAt
            ? application
            : { ...application, planningConfirmedAt: Date.now() }
          const persistedApplication =
            confirmedApplication !== application
              ? await saveApplication(confirmedApplication)
              : confirmedApplication
          planningController.dismissPlanning(application.id)
          await openWorkbench(persistedApplication, lifecycle)
          return
        }
        // 模板已生成（ready_for_workbench）但用户尚未确认进入开发：进设计阶段，
        // 用持久化的 planningThreadId 恢复规划会话以回显历史卡片。
        // 后端在 ready_for_workbench 时清空了 lifecycle.threadId，故优先用 application.planningThreadId。
        const threadId = lifecycle?.initialization?.threadId || application.planningThreadId
        if (threadId) {
          // 已有活动规划（之前进入过、Modal 仍挂载）则直接进工作台，不重复 startPlanning，
          // 避免重置 workflow 快照导致右侧 UI 设计稿预览和确认卡丢失。
          const existingPlanning = planningController.activePlannings.find(
            (planning) => planning.application.id === application.id
          )
          if (existingPlanning) {
            await openWorkbench(existingPlanning.application, lifecycle)
            return
          }
          planningController.startPlanning(application, threadId, lifecycle, false)
          await openWorkbench(application, lifecycle)
          return
        }
      } catch (error) {
        console.warn('读取或校验应用 readiness 失败', error)
        message.error(error instanceof Error ? error.message : '应用 readiness 校验失败')
        return
      }
      const activePlanning = planningController.activePlannings.find(
        (planning) => planning.application.id === application.id
      )
      if (activePlanning) {
        // 已有活动规划（Modal 后台挂载跑 graph）：直接进工作台，不重复 startPlanning。
        await openWorkbench(activePlanning.application, activePlanning.lifecycle)
        return
      }
      message.info('请先完成并确认应用计划')
    },
    [openWorkbench, planningController]
  )

  const planningVisible = Boolean(planningController.visiblePlanningId)
  const mountedPlannings = launchContext
    ? planningController.activePlannings.filter(
        (planning) =>
          planning.application.id === launchContext.applicationId &&
          planning.threadId === launchContext.graphThreadId
      )
    : planningController.activePlannings

  return (
    <>
      <div
        aria-hidden={Boolean(launchContext) || activeSurface !== 'welcome' || planningVisible}
        hidden={Boolean(launchContext) || activeSurface !== 'welcome' || planningVisible}
      >
        <WelcomePage
          activePlannings={planningController.activePlannings}
          deletingPlanningIds={planningController.deletingPlanningIds}
          onDeletePlanning={planningController.removePlanning}
          onOpenApplication={handleOpenApplication}
          onOpenPlanning={(applicationId) => {
            const planning = planningController.activePlannings.find(
              (item) => item.application.id === applicationId
            )
            if (planning) {
              void handleOpenApplication(planning.application)
            } else {
              planningController.showPlanning(applicationId)
            }
          }}
          onStartPlanning={handleOpenWorkbenchAfterCreate}
          theme={theme}
        />
      </div>

      {launchContext && !activeApplication ? <PlanningWindowBootScreen theme={theme} /> : null}

      {mountedPlannings.map((planning) => (
        <ApplicationPagePlanningModal
          application={planning.application}
          initialLifecycle={planning.lifecycle}
          initialStatus={planning.status}
          initialWorkflow={planning.workflow}
          key={planning.threadId}
          onTechnicalPlanConfirmed={() =>
            planningController.onTechnicalPlanConfirmed(planning.application.id)
          }
          onErrorChange={(error) =>
            planningController.updatePlanningError(planning.application.id, error)
          }
          onLifecycleChange={(lifecycle) => {
            planningController.updatePlanningLifecycle(planning.application.id, lifecycle)
            if (activeApplication?.id === planning.application.id) {
              mergeApplicationLifecycle(lifecycle)
            }
          }}
          onSubmitClarificationChange={(handler) => {
            planningSubmitByAppRef.current[planning.application.id] = handler ?? undefined
            setPlanningSubmitRevision((current) => current + 1)
            if (!handler) return
            const pending = pendingPlanningSubmitByAppRef.current[planning.application.id]
            if (!pending) return
            delete pendingPlanningSubmitByAppRef.current[planning.application.id]
            void handler(
              pending.workflow,
              pending.answers,
              pending.editedRequirementSpec,
              pending.requirementSpecFeedback,
              pending.designChangeRequest
            ).then(pending.resolve, pending.reject)
          }}
          onStartDesignRevisionChange={(handler) => {
            planningDesignRevisionByAppRef.current[planning.application.id] = handler ?? undefined
            const pending = pendingDesignRevisionByAppRef.current[planning.application.id]
            if (!handler || !pending) return
            delete pendingDesignRevisionByAppRef.current[planning.application.id]
            window.clearTimeout(pending.timer)
            void handler(pending.input).then(pending.resolve, pending.reject)
          }}
          onRevisionContinuation={(handoff) =>
            dispatchRevisionContinuation(planning.application.id, handoff)
          }
          onPlanningContent={(content) => {
            deliverPlanningChunk(planning.threadId, { content, workflow: undefined })
          }}
          onPlanningWorkflow={(workflow) => {
            deliverPlanningChunk(planning.threadId, { content: undefined, workflow })
          }}
          onReturnHome={planningController.returnHome}
          onStatusChange={(status) =>
            planningController.updatePlanningStatus(planning.application.id, status)
          }
          onStopHandlerChange={(handler) =>
            planningController.registerStopHandler(planning.application.id, handler)
          }
          onRetryHandlerChange={(handler) => {
            planningRetryByAppRef.current[planning.application.id] = handler ?? undefined
          }}
          onWorkflowChange={(workflow) =>
            planningController.updatePlanningWorkflow(planning.application.id, workflow)
          }
          theme={theme}
          threadId={planning.threadId}
          visible={planningController.visiblePlanningId === planning.application.id}
        />
      ))}

      {activeApplication ? (
        <div
          aria-hidden={activeSurface !== 'workbench'}
          hidden={activeSurface !== 'workbench'}
          key={activeApplication.id}
        >
          <WorkbenchPage
            application={activeApplication}
            applicationLifecycle={applicationLifecycle}
            onApplicationLifecycleChange={mergeApplicationLifecycle}
            onReturnWelcome={handleReturnWelcome}
            onSubmitPlanningClarification={(
              workflow,
              answers,
              editedRequirementSpec,
              requirementSpecFeedback,
              designChangeRequest
            ): Promise<void> => {
              const submit = planningSubmitByAppRef.current[activeApplication.id]
              if (!submit) {
                // Modal 尚未注册句柄时保留最新一次用户提交；注册回调会负责补发。
                return new Promise<void>((resolve, reject) => {
                  const previous = pendingPlanningSubmitByAppRef.current[activeApplication.id]
                  previous?.reject(new Error('规划提交已被更新的用户操作替代。'))
                  pendingPlanningSubmitByAppRef.current[activeApplication.id] = {
                    workflow,
                    answers,
                    editedRequirementSpec,
                    requirementSpecFeedback,
                    designChangeRequest,
                    resolve,
                    reject
                  }
                })
              }
              return submit(
                workflow,
                answers,
                editedRequirementSpec,
                requirementSpecFeedback,
                designChangeRequest
              )
            }}
            onStopPlanning={() => planningController.stopPlanning(activeApplication.id)}
            onStartDesignStageRevision={(input) =>
              handleStartDesignStageRevision(activeApplication, input)
            }
            onRevisionContinuationHandlerChange={(handler) => {
              revisionContinuationByAppRef.current[activeApplication.id] = handler ?? undefined
              const pending = pendingRevisionContinuationByAppRef.current[activeApplication.id]
              if (!handler || !pending) return
              delete pendingRevisionContinuationByAppRef.current[activeApplication.id]
              void handler(pending.handoff).then(pending.resolve, pending.reject)
            }}
            onThemeChange={setTheme}
            onPlanningStreamReady={handlePlanningStreamReady}
            onRetryPlanning={
              templateGenerationFailed
                ? undefined
                : () => {
                    const retry = planningRetryByAppRef.current[activeApplication.id]
                    if (retry) retry()
                    else planningController.showPlanning(activeApplication.id)
                  }
            }
            generatingTemplate={planningController.generatingAppIds.has(activeApplication.id)}
            planningThreadId={activePlanningThreadId}
            initialPhase={launchContext?.phase}
            planningConversationThreadId={
              launchContext?.applicationId === activeApplication.id
                ? launchContext.conversationThreadId
                : undefined
            }
            planningWorkflow={activePlanning?.workflow}
            planningError={activePlanning?.error}
            theme={theme}
          />
        </div>
      ) : null}
    </>
  )
}

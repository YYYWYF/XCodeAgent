import { useCallback, useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import { saveApplication } from '../components/Welcome/applicationService'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { useActiveApplicationPlannings } from '../hooks/useActiveApplicationPlannings'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
import { useApplicationTheme } from '../hooks/useApplicationTheme'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { canOpenApplicationWorkbench } from '../service/applicationStorage'
import { stopProjectPreview } from '../service/projectLaunch'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../typings'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'

type ActiveSurface = 'welcome' | 'workbench'

/** 读取应用实际绑定的预览工作区，用于区分不同生成项目进程。 */
function applicationPreviewWorkspace(application: ApplicationConfig): string {
  return application.workspaceRoot || application.projectParentPath || ''
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
          requirementSpecFeedback?: string
        ) => void)
      | undefined
    >
  >({})

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

  // 同步当前活动工作台的规划线程标识到 ref，供 deliverPlanningChunk 按 threadId 过滤。
  // 只有匹配该 threadId 的规划流式才注入工作台，避免后台其他应用规划串入对话。
  const activePlanningThreadId = activeApplication
    ? planningController.activePlannings.find(
        (item) => item.application.id === activeApplication.id
      )?.threadId
    : undefined
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
      const enterDevConfirmed =
        window.localStorage.getItem(`xcodeagent:enter-dev-confirmed:${application.id}`) === '1'
      if (canOpenApplicationWorkbench(application) && enterDevConfirmed) {
        // 从历史列表打开且用户已确认进入开发：直接进工作台开发阶段。
        planningController.dismissPlanning(application.id)
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
        console.warn('读取应用生命周期失败', error)
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

  return (
    <>
      <div
        aria-hidden={activeSurface !== 'welcome' || planningVisible}
        hidden={activeSurface !== 'welcome' || planningVisible}
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

      {planningController.activePlannings.map((planning) => (
        <ApplicationPagePlanningModal
          application={planning.application}
          initialLifecycle={planning.lifecycle}
          initialStatus={planning.status}
          initialWorkflow={planning.workflow}
          key={planning.threadId}
          onConfirmed={() => planningController.onPlanningConfirmed(planning.application.id)}
          onSubmitClarificationChange={(handler) => {
            planningSubmitByAppRef.current[planning.application.id] = handler ?? undefined
          }}
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
              requirementSpecFeedback
            ) => {
              const submit = planningSubmitByAppRef.current[activeApplication.id]
              if (submit)
                submit(workflow, answers, editedRequirementSpec, requirementSpecFeedback)
            }}
            onThemeChange={setTheme}
            onPlanningStreamReady={handlePlanningStreamReady}
            onRetryTemplate={() => {
              void planningController.onPlanningConfirmed(activeApplication.id)
            }}
            generatingTemplate={planningController.generatingAppIds.has(activeApplication.id)}
            planningThreadId={activePlanningThreadId}
            planningWorkflow={
              planningController.activePlannings.find(
                (item) => item.application.id === activeApplication.id
              )?.workflow
            }
            theme={theme}
          />
        </div>
      ) : null}
    </>
  )
}

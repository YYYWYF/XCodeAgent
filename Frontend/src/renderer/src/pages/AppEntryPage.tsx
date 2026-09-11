import { useCallback, useRef, useState } from 'react'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { useActiveApplicationPlannings } from '../hooks/useActiveApplicationPlannings'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
import { useApplicationPlanningRuntimes } from '../hooks/useApplicationPlanningRuntimes'
import { useApplicationPlanningStreamingContent } from '../hooks/useApplicationPlanningStreamingContent'
import { useApplicationPlanningWorkbenchBridge } from '../hooks/useApplicationPlanningWorkbenchBridge'
import { useApplicationTheme } from '../hooks/useApplicationTheme'
import {
  workflowApplicationLifecycle,
  type ApplicationPlanningCurrentEvent
} from '../service/activeApplicationPlanning'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { isTemplateGenerationOrphaned } from '../service/templateApi'
import { stopProjectPreview } from '../service/projectLaunch'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart
} from '../typings'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'
import { hasApplicationEnteredDevelopment } from '../workbenchPhase'

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

// 在欢迎页、独立后台规划 Runtime 与应用工作台之间维护顶层导航。
function AppEntryContent(): JSX.Element {
  const { theme, setTheme } = useApplicationTheme()
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activeSurface, setActiveSurface] = useState<ActiveSurface>('welcome')
  const activePreviewWorkspaceRef = useRef('')
  const { lifecycle: applicationLifecycle, mergeLifecycle: mergeApplicationLifecycle } =
    useApplicationLifecycleStore(activeApplication?.id || '')

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
    onApplicationLifecycleChange: mergeApplicationLifecycle,
    onOpenWorkbench: openWorkbench
  })

  // 将 Runtime 产生的事件提交给唯一 Planning Store，并同步当前工作台的全局 lifecycle 投影。
  const handlePlanningCurrentEvent = useCallback(
    (event: ApplicationPlanningCurrentEvent): void => {
      planningController.dispatchPlanningEvent(event)
      if (activeApplication?.id !== event.applicationId) return
      const lifecycle =
        event.type === 'reconcile_received'
          ? event.lifecycle
          : event.type === 'lifecycle_received'
          ? event.lifecycle
          : event.type === 'workflow_received'
            ? workflowApplicationLifecycle(event.workflow)
            : event.type === 'run_failed' && event.workflow
              ? workflowApplicationLifecycle(event.workflow)
              : undefined
      if (lifecycle) mergeApplicationLifecycle(lifecycle)
    },
    [activeApplication?.id, mergeApplicationLifecycle, planningController.dispatchPlanningEvent]
  )

  const activePlanning = activeApplication
    ? planningController.getPlanningState(activeApplication.id)
    : undefined
  const activePlanningLifecycle = activePlanning?.lifecycle || applicationLifecycle
  const templateGenerationFailed =
    activePlanningLifecycle?.initialization.stage === 'application_template_generation_failed'
  const templateGenerationOrphaned = isTemplateGenerationOrphaned(
    activePlanningLifecycle,
    activeApplication
      ? planningController.generatingAppIds.has(activeApplication.id)
      : false
  )
  const templateGenerationRecoverable =
    templateGenerationFailed || templateGenerationOrphaned

  const {
    deliverPlanningChunk,
    dispatchRevisionContinuation,
    handlePlanningStreamReady,
    registerRevisionContinuation
  } = useApplicationPlanningWorkbenchBridge(activePlanning?.threadId)

  const planningRuntimeController = useApplicationPlanningRuntimes({
    activePlannings: planningController.activePlannings,
    getPlanningState: planningController.getPlanningState,
    dispatchPlanningEvent: handlePlanningCurrentEvent,
    /** Runtime 先更新当前状态，再将正文交给匹配线程的工作台历史。 */
    publishContent: (applicationId, content) => {
      const current = planningController.getPlanningState(applicationId)
      if (current) deliverPlanningChunk(current.threadId, { content })
    },
    /** 历史投递只消费 Runtime 发布的快照，不承担当前状态写入。 */
    publishWorkflow: (_applicationId, workflow) => {
      deliverPlanningChunk(workflow.threadId, { workflow })
    },
    /** 技术规划确认继续复用唯一的模板生成编排。 */
    onTechnicalPlanConfirmed: (applicationId) =>
      planningController.onTechnicalPlanConfirmed(applicationId),
    onRevisionContinuation: dispatchRevisionContinuation
  })

  // 设计阶段二次修改直接确保原 planning thread 的 Runtime，并立即发起本轮动作。
  const handleStartDesignStageRevision = useCallback(
    async (
      application: ApplicationConfig,
      input: WorkflowDesignStageRevisionStart
    ): Promise<void> => {
      let planning = planningController.getPlanningState(application.id)
      if (!planning) {
        const lifecycle = await getApplicationLifecycle(application)
        const threadId = application.planningThreadId || lifecycle.initialization.threadId
        if (!threadId) throw new Error('当前应用缺少原 planning thread，无法返回设计阶段。')
        planning = planningController.startPlanning(application, threadId, lifecycle, false)
      }
      planningRuntimeController.ensureRuntime(planning)
      await planningRuntimeController.startDesignRevision(application.id, input)
    },
    [
      planningController.getPlanningState,
      planningController.startPlanning,
      planningRuntimeController
    ]
  )

  const visiblePlanning = planningController.visiblePlanningId
    ? planningController.getPlanningState(planningController.visiblePlanningId)
    : undefined
  const streamingContent = useApplicationPlanningStreamingContent(
    visiblePlanning ? planningRuntimeController.getRuntime(visiblePlanning.application.id) : undefined
  )

  // 新建应用：登记规划状态后直接进入工作台，后台执行由根部 Runtime Manager 接管。
  const handleOpenWorkbenchAfterCreate = useCallback(
    (application: ApplicationConfig, threadId: string, lifecycle: ApplicationLifecycle): void => {
      planningController.startPlanning(application, threadId, lifecycle, false)
      void openWorkbench(application, lifecycle)
    },
    [openWorkbench, planningController]
  )

  // 从工作台直接返回欢迎页，后台任务由工作台会话与独立 Planning Runtime 继续运行。
  const handleReturnWelcome = (): void => {
    setActiveSurface('welcome')
  }

  // 工作台首次加载失败时卸载本次工作台实例，确保再次打开应用会重新执行完整恢复。
  const handleWorkbenchEntryFailure = useCallback((): void => {
    setActiveSurface('welcome')
    setActiveApplication(null)
  }, [])

  // 所有应用都直接进入工作台；新建应用同时恢复其当前设计或规划会话。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      if (application.source !== 'new') {
        await openWorkbench(application)
        return
      }

      const existingPlanning = planningController.getPlanningState(application.id)
      if (existingPlanning) {
        await openWorkbench(existingPlanning.application, existingPlanning.lifecycle)
        return
      }

      try {
        const lifecycle = await getApplicationLifecycle(application)
        const readyForWorkbench = lifecycle?.initialization?.stage === 'ready_for_workbench'
        if (readyForWorkbench && hasApplicationEnteredDevelopment(application.id)) {
          planningController.dismissPlanning(application.id)
          await openWorkbench(application, lifecycle)
          return
        }

        // 后端在模板就绪后会清空 lifecycle.threadId；仍用应用持久化的原线程恢复规划历史。
        const threadId = lifecycle?.initialization?.threadId || application.planningThreadId
        if (threadId) {
          planningController.startPlanning(application, threadId, lifecycle, false, true)
        }
        await openWorkbench(application, lifecycle)
      } catch (error) {
        // 生命周期读取失败不能再成为工作台入口门禁；工作台内部仍可通过实时事件继续校准。
        console.warn('读取应用生命周期失败，直接进入工作台。', error)
        await openWorkbench(application)
      }
    },
    [openWorkbench, planningController]
  )

  const planningVisible = Boolean(visiblePlanning)

  return (
    <>
      <div
        aria-hidden={activeSurface !== 'welcome' || planningVisible}
        hidden={activeSurface !== 'welcome' || planningVisible}
      >
        <WelcomePage
          onBeforeDeleteApplication={async (application) => {
            planningController.dismissPlanning(application.id)
          }}
          onOpenApplication={handleOpenApplication}
          onStartPlanning={handleOpenWorkbenchAfterCreate}
          theme={theme}
        />
      </div>

      {visiblePlanning ? (
        <ApplicationPagePlanningModal
          key={`${visiblePlanning.application.id}:${visiblePlanning.threadId}`}
          planning={visiblePlanning}
          streamingContent={streamingContent}
          generatingTemplate={planningController.generatingAppIds.has(
            visiblePlanning.application.id
          )}
          onSubmit={(...args) =>
            planningRuntimeController.submitClarification(visiblePlanning.application.id, ...args)
          }
          onSaveRequirementSpec={(spec) =>
            planningRuntimeController.saveRequirementSpec(visiblePlanning.application.id, spec)
          }
          onRetry={() =>
            void (visiblePlanning.syncError
              ? planningRuntimeController.reconcileCurrentState(visiblePlanning.application.id)
              : planningRuntimeController.retryCurrentFailure(visiblePlanning.application.id))
          }
          onReturnHome={planningController.returnHome}
          theme={theme}
          visible
        />
      ) : null}

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
            onEntryLoadFailure={handleWorkbenchEntryFailure}
            onReturnWelcome={handleReturnWelcome}
            onSubmitPlanningClarification={(...args) =>
              planningRuntimeController.submitClarification(activeApplication.id, ...args)
            }
            onStartDesignStageRevision={(input) =>
              handleStartDesignStageRevision(activeApplication, input)
            }
            onRevisionContinuationHandlerChange={(handler) =>
              registerRevisionContinuation(activeApplication.id, handler)
            }
            onThemeChange={setTheme}
            onPlanningStreamReady={handlePlanningStreamReady}
            onSavePlanningRequirementSpec={(spec) =>
              planningRuntimeController.saveRequirementSpec(activeApplication.id, spec)
            }
            onStopPlanning={() => planningRuntimeController.stop(activeApplication.id)}
            onRetryPlanning={
              activePlanning?.syncError
                ? () => void planningRuntimeController.reconcileCurrentState(activeApplication.id)
                : templateGenerationRecoverable
                  ? () => {
                      void planningController.retryTemplateGeneration(activeApplication.id)
                    }
                  : () => void planningRuntimeController.retryCurrentFailure(activeApplication.id)
            }
            generatingTemplate={planningController.generatingAppIds.has(activeApplication.id)}
            planningState={activePlanning}
            theme={theme}
          />
        </div>
      ) : null}
    </>
  )
}

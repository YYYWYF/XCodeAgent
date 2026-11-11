import { useCallback, useRef, useState } from 'react'
import {
  SessionRuntimeProvider,
  useSessionRuntimeStore
} from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { useActiveApplicationPlannings } from '../hooks/useActiveApplicationPlannings'
import {
  hasNonTerminalApplicationExecution,
  useApplicationLifecycleStore
} from '../hooks/useApplicationLifecycleStore'
import { useApplicationPlanningRuntimes } from '../hooks/useApplicationPlanningRuntimes'
import { technicalPlanningReplyPresentation } from '../service/technicalPlanningConversation'
import { useApplicationPlanningStreamingContent } from '../hooks/useApplicationPlanningStreamingContent'
import { useApplicationPlanningWorkbenchBridge } from '../hooks/useApplicationPlanningWorkbenchBridge'
import { useApplicationTheme } from '../hooks/useApplicationTheme'
import { UiReadVisibility } from '../hooks/uiReadVisibility'
import {
  workflowApplicationLifecycle,
  type ApplicationPlanningCurrentEvent
} from '../service/activeApplicationPlanning'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { applicationWorkspaceKey, sameApplicationWorkspace } from '../service/applicationWorkspace'
import { isTemplateGenerationOrphaned, isTemplateReconcileRetryable } from '../service/templateApi'
import { stopProjectPreview } from '../service/projectLaunch'
import { leavePreviewRuntime } from '../service/previewRuntime'
import { useReturnWelcome } from '../hooks/useReturnWelcome'
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
  const { releasePreviewMaintenanceExecutions } = useSessionRuntimeStore()
  const { theme, setTheme } = useApplicationTheme()
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activeSurface, setActiveSurface] = useState<ActiveSurface>('welcome')
  const activePreviewWorkspaceRef = useRef('')
  const openRequestRef = useRef(0)
  const {
    lifecycle: applicationLifecycle,
    mergeLifecycle: mergeApplicationLifecycle,
    resetLifecycle: resetApplicationLifecycle
  } = useApplicationLifecycleStore(activeApplication?.id || '', activeApplication?.workspaceRoot)

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
        void window.devAgentStudio?.projectPreview?.unregisterWorkspace({
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
      if (event.workspaceRoot !== undefined && !sameApplicationWorkspace(activeApplication, { id: event.applicationId, workspaceRoot: event.workspaceRoot })) return
      const lifecycle =
        event.type === 'reconcile_received'
          ? event.lifecycle
          : event.type === 'lifecycle_received' || event.type === 'planning_ended'
            ? event.lifecycle
            : event.type === 'workflow_received' || event.type === 'stage_thread_handed_off'
              ? workflowApplicationLifecycle(event.workflow)
              : event.type === 'run_failed' && event.workflow
                ? workflowApplicationLifecycle(event.workflow)
                : undefined
      if (lifecycle) mergeApplicationLifecycle(lifecycle)
    },
    [activeApplication, mergeApplicationLifecycle, planningController.dispatchPlanningEvent]
  )

  const registeredPlanning = activeApplication
    ? planningController.getPlanningState(activeApplication.id)
    : undefined
  const activePlanning = sameApplicationWorkspace(activeApplication || undefined, registeredPlanning?.application) ? registeredPlanning : undefined

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
    /** 只读答复只写可见历史，不作为当前 checkpoint 的状态快照。 */
    publishReply: (_applicationId, reply) => {
      deliverPlanningChunk(reply.planningThreadId, { workflow: technicalPlanningReplyPresentation(reply) })
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
      input: WorkflowDesignStageRevisionStart,
      target?: import('../service/planningStageHandoff').PlanningStageTarget
    ): Promise<void> => {
      let planning = planningController.getPlanningState(application.id)
      if (!planning || !sameApplicationWorkspace(planning.application, application)) {
        const lifecycle = await getApplicationLifecycle(application)
        const threadId = lifecycle.initialization.threadId
        if (!threadId) throw new Error('当前应用缺少原 planning thread，无法返回设计阶段。')
        planning = planningController.startPlanning(application, threadId, lifecycle, false)
      }
      planningRuntimeController.ensureRuntime(planning)
      await planningRuntimeController.startDesignRevision(application.id, input, target)
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
    visiblePlanning
      ? planningRuntimeController.getRuntime(visiblePlanning.application.id)
      : undefined
  )

  // 新建应用：登记规划状态后直接进入工作台，后台执行由根部 Runtime Manager 接管。
  const handleOpenWorkbenchAfterCreate = useCallback(
    (application: ApplicationConfig, threadId: string, lifecycle: ApplicationLifecycle): void => {
      planningController.startPlanning(application, threadId, lifecycle, false)
      void openWorkbench(application, lifecycle)
    },
    [openWorkbench, planningController]
  )

  // 返回欢迎页时立即触发预览维护释放和前后端双重停止，不等待后台清理即可导航。
  /** 真正离开工作台：清理预览维护后切回欢迎页。 */
  const leaveWorkbench = useCallback((): void => {
    const workspace = activeApplication ? applicationPreviewWorkspace(activeApplication) : ''
    releasePreviewMaintenanceExecutions(workspace)
    activePreviewWorkspaceRef.current = ''
    if (workspace) {
      void leavePreviewRuntime(workspace).catch((error) => {
        console.warn('退出工作台时清理预览维护失败。', error)
      })
    }
    setActiveSurface('welcome')
  }, [activeApplication, releasePreviewMaintenanceExecutions])

  const { returningWelcome, handleReturnWelcome } = useReturnWelcome({
    applicationKey: activeApplication ? applicationWorkspaceKey(activeApplication) : '',
    workspace: activeApplication ? applicationPreviewWorkspace(activeApplication) : '',
    enabled: activeSurface === 'workbench',
    hasActiveExecution: hasNonTerminalApplicationExecution(applicationLifecycle),
    onLeave: leaveWorkbench
  })

  // 工作台首次加载失败时卸载本次工作台实例，确保再次打开应用会重新执行完整恢复。
  const handleWorkbenchEntryFailure = useCallback((): void => {
    setActiveSurface('welcome')
    setActiveApplication(null)
  }, [])

  // 所有应用都直接进入工作台；已保存但尚未完成创建规划的应用也恢复原会话。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      const requestId = ++openRequestRef.current
      const existingPlanning = planningController.getPlanningState(application.id)
      if (existingPlanning && sameApplicationWorkspace(existingPlanning.application, application)) {
        await openWorkbench(existingPlanning.application, existingPlanning.lifecycle)
        return
      }
      // 切换到克隆目录时只释放旧前端状态和订阅，原后端任务仍由自己的工作区持有。
      if (existingPlanning) planningController.dismissPlanning(application.id)

      try {
        const lifecycle = await getApplicationLifecycle(application)
        if (requestId !== openRequestRef.current) return
        const readyForWorkbench = lifecycle?.initialization?.stage === 'ready_for_workbench'
        // 进入开发门禁按「应用 + 当前分支」隔离：新迭代已就绪但本轮还没进过开发时，
        // 不能跳过规划状态恢复，否则就绪卡与"进入开发阶段"入口都不会出现。
        if (
          readyForWorkbench &&
          !lifecycle.endedPlanningExecution &&
          hasApplicationEnteredDevelopment(
            application.id,
            application.branchName || application.id
          )
        ) {
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
        if (requestId !== openRequestRef.current) return
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
          generatingTemplate={planningController.generatingWorkspaceKeys.has(
            applicationWorkspaceKey(visiblePlanning.application)
          )}
          onSubmit={(...args) =>
            planningRuntimeController.submitClarification(visiblePlanning.application.id, ...args)
          }
          onSaveRequirementSpec={(spec) =>
            planningRuntimeController.saveRequirementSpec(visiblePlanning.application.id, spec)
          }
          onRetry={() => {
            const generationFailed = visiblePlanning.lifecycle.initialization.stage === 'application_template_generation_failed' ||
              isTemplateGenerationOrphaned(visiblePlanning.lifecycle, planningController.generatingWorkspaceKeys.has(applicationWorkspaceKey(visiblePlanning.application)))
            if (generationFailed) {
              void planningController.retryTemplateGeneration(visiblePlanning.application.id)
            } else if (isTemplateReconcileRetryable(visiblePlanning.lifecycle, visiblePlanning.workflow?.summary.templatePreparation)) {
              void planningRuntimeController.retryTemplateReconcile(visiblePlanning.application.id)
            } else {
              void planningRuntimeController.retryCurrentFailure(visiblePlanning.application.id)
            }
          }}
          onReconnect={() => {
            void planningRuntimeController.reconcileCurrentState(visiblePlanning.application.id).catch(console.error)
          }}
          onReturnHome={planningController.returnHome}
          theme={theme}
          visible
        />
      ) : null}

      {activeApplication ? (
        <div
          aria-hidden={activeSurface !== 'workbench'}
          hidden={activeSurface !== 'workbench'}
          key={applicationWorkspaceKey(activeApplication)}
        >
          <UiReadVisibility.Provider value={activeSurface === 'workbench'}>
            <WorkbenchPage
              application={activeApplication}
              applicationLifecycle={applicationLifecycle}
              onApplicationLifecycleChange={mergeApplicationLifecycle}
              onApplicationLifecycleReset={resetApplicationLifecycle}
              onEntryLoadFailure={handleWorkbenchEntryFailure}
              onReturnWelcome={handleReturnWelcome}
              returningWelcome={returningWelcome}
              onSubmitPlanningClarification={(...args) =>
                planningRuntimeController.submitClarification(activeApplication.id, ...args)
              }
              onStartDesignStageRevision={(input, target) =>
                handleStartDesignStageRevision(activeApplication, input, target)
              }
              onIterationStarted={(application, threadId, lifecycle) => {
                planningController.startPlanning(application, threadId, lifecycle, false, false)
              }}
              onStartIterationPlanning={(applicationId, request, workflowDebug, debugStage) =>
                planningRuntimeController.startIterationPlanning(
                  applicationId,
                  request,
                  workflowDebug,
                  debugStage
                )
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
              onRetryPlanning={(restartInitialPlanning) =>
                planningRuntimeController.retryCurrentFailure(activeApplication.id, restartInitialPlanning)}
              onReconcilePlanning={() => {
                void planningRuntimeController.reconcileCurrentState(activeApplication.id).catch(console.error)
              }}
              onRetryTemplateReconcile={() =>
                void planningRuntimeController.retryTemplateReconcile(activeApplication.id)
              }
              onRetryTemplateGeneration={() => {
                void planningController.retryTemplateGeneration(activeApplication.id)
              }}
              generatingTemplate={planningController.generatingWorkspaceKeys.has(applicationWorkspaceKey(activeApplication))}
              planningState={activePlanning}
              theme={theme}
            />
          </UiReadVisibility.Provider>
        </div>
      ) : null}
    </>
  )
}

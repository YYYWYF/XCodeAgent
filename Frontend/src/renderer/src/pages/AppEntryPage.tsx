import { useCallback, useRef, useState } from 'react'
import { message, Modal } from 'antd'
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
import { leavePreviewRuntime } from '../service/previewRuntime'
import { inspectAllVersionControl } from '../service/versionControl'
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
  const {
    lifecycle: applicationLifecycle,
    mergeLifecycle: mergeApplicationLifecycle,
    resetLifecycle: resetApplicationLifecycle
  } = useApplicationLifecycleStore(activeApplication?.id || '')

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
    activeApplication ? planningController.generatingAppIds.has(activeApplication.id) : false
  )
  const templateGenerationRecoverable = templateGenerationFailed || templateGenerationOrphaned

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

  /**
   * 中等提示：返回欢迎页前，若有已验证但未提交的变更则确认一次。
   *
   * 这里**当场重新读取** Git 状态，而不是复用工作台里的常驻快照 —— 即将离开工作台，
   * 常驻快照可能已过期，而"是否还有未提交代码"正是这次判断的全部依据。
   *
   * Agent 仍在运行时只提示、不阻断、也不启动提交（代码还没写完，提交没有意义）。
   */
  const handleReturnWelcome = useCallback(async (): Promise<void> => {
    const workspace = activeApplication ? applicationPreviewWorkspace(activeApplication) : ''
    if (!workspace) {
      leaveWorkbench()
      return
    }
    if (hasNonTerminalApplicationExecution(applicationLifecycle)) {
      message.info('当前有任务正在执行，返回首页后会在后台继续。')
      leaveWorkbench()
      return
    }
    let pending = 0
    try {
      // 只算业务代码：`.xcodeagent` 平台产物（规划文档、状态快照）不算"用户改了代码"，
      // 否则新建迭代清空产物后，一进工作台就会因为这个弹窗被拦一次。
      pending = (await inspectAllVersionControl(workspace)).codePaths.length
    } catch {
      // 读不到 Git（未建仓库等）时直接返回，不因为辅助提示挡住导航。
      leaveWorkbench()
      return
    }
    if (pending === 0) {
      leaveWorkbench()
      return
    }
    Modal.confirm({
      centered: true,
      title: '仍有未提交的代码变更',
      content: `当前工作区有 ${pending} 个文件未提交。返回首页不会丢失代码，但建议先提交形成可追溯版本。`,
      okText: '仍然返回',
      cancelText: '取消',
      onOk: () => leaveWorkbench()
    })
  }, [activeApplication, applicationLifecycle, leaveWorkbench])

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
        // 进入开发门禁按「应用 + 当前迭代版本」隔离：新迭代已就绪但本轮还没进过开发时，
        // 不能跳过规划状态恢复，否则就绪卡与"进入开发阶段"入口都不会出现。
        if (
          readyForWorkbench &&
          hasApplicationEnteredDevelopment(
            application.id,
            application.currentVersionId || application.id
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
            onApplicationLifecycleReset={resetApplicationLifecycle}
            onEntryLoadFailure={handleWorkbenchEntryFailure}
            onReturnWelcome={handleReturnWelcome}
            onSubmitPlanningClarification={(...args) =>
              planningRuntimeController.submitClarification(activeApplication.id, ...args)
            }
            onStartDesignStageRevision={(input) =>
              handleStartDesignStageRevision(activeApplication, input)
            }
            onIterationStarted={(application, threadId, lifecycle) => {
              planningController.startPlanning(application, threadId, lifecycle, false, false)
            }}
            onStartIterationPlanning={(applicationId, request) =>
              planningRuntimeController.startIterationPlanning(applicationId, request)
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
            onRetryTemplateReconcile={() =>
              void planningRuntimeController.retryTemplateReconcile(activeApplication.id)
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

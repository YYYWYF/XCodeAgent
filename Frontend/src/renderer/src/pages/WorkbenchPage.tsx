import { Layout } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import { WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  isApplicationCreationComplete,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type { WorkflowRevisionContinuationHandoff } from '../service/applicationPagePlanning'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import { cx, previewOrigin } from '../utils'
import { startProjectLaunch, stopProjectPreview } from '../service/projectLaunch'
import {
  hasApplicationEnteredDevelopment,
  subscribeApplicationDevelopmentEntry,
  type WorkbenchPhase
} from '../workbenchPhase'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onReturnWelcome: () => void
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => Promise<void>
  onStopPlanning: () => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: Theme) => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 设计阶段后台规划窗口的模型错误。 */
  planningError?: string
  /** 从工作台错误卡片重新打开设计阶段规划窗口。 */
  onRetryPlanning?: () => void
  planningThreadId?: string
  planningWorkflow?: WorkflowRunPayload
  /** 独立阶段窗口的首屏阶段，避免 lifecycle 拉取前短暂显示研发阶段。 */
  initialPhase?: WorkbenchPhase
  /** 规划 Agent 独立聊天会话标识，不替代后端 Graph checkpoint thread。 */
  planningConversationThreadId?: string
  theme: Theme
}

type Theme = 'light' | 'dark'
type WorkbenchEntryStage = 'loading' | 'leaving' | 'ready'

const WORKBENCH_ENTRY_MIN_VISIBLE_MS = 520
const WORKBENCH_ENTRY_FADE_MS = 280

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({
  application,
  applicationLifecycle,
  onApplicationLifecycleChange,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStopPlanning,
  onStartDesignStageRevision,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  generatingTemplate,
  planningError,
  onRetryPlanning,
  planningThreadId,
  planningWorkflow,
  initialPhase,
  planningConversationThreadId,
  theme
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false)
  const [hasPageDesigns, setHasPageDesigns] = useState(false)
  const [developmentPlanningPages, setDevelopmentPlanningPages] = useState<
    DevelopmentPlanningPageOption[]
  >([])
  const [developmentPlanningPageTree, setDevelopmentPlanningPageTree] = useState<
    DevelopmentPlanningPageTreeNode[]
  >([])
  const [developmentPlanningApiContracts, setDevelopmentPlanningApiContracts] = useState<
    DevelopmentPlanningApiContract[]
  >([])
  const [developmentPlanningEntities, setDevelopmentPlanningEntities] = useState<
    DevelopmentPlanningEntityOption[]
  >([])
  const [chatSessionHistoryReady, setChatSessionHistoryReady] = useState(false)
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  const [previewBaseUrl, setPreviewBaseUrl] = useState('')
  const [previewLaunchError, setPreviewLaunchError] = useState('')
  // 预览启动中状态：驱动左侧上下文头“预览页面”按钮的 loading 呈现。
  const [previewLaunchLoading, setPreviewLaunchLoading] = useState(false)
  const stageWindow = initialPhase === 'planning'
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>(() =>
    stageWindow ? 'ready' : 'loading'
  )
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const entryStartedAtRef = useRef(Date.now())
  const launchedWorkspaceRef = useRef<string>()
  const activeLaunchWorkspaceRef = useRef('')
  const launchRunIdRef = useRef(0)
  const launchCleanupPendingRef = useRef(false)
  const launchCleanupTimerRef = useRef<number>()
  const [developmentEntryConfirmed, setDevelopmentEntryConfirmed] = useState(
    () => application.source !== 'new' || hasApplicationEnteredDevelopment(application.id)
  )
  // 模板是否就绪（lifecycle=ready_for_workbench）。用 boolean 而非整个 lifecycle 作为预览启动
  // effect 的依赖，避免规划期流式 workflow 事件频繁递增 revision 导致 effect 反复 cleanup，
  // 进而中断正在进行的 npm install / dev server 启动。
  const lifecycleReadyForWorkbench = isApplicationCreationComplete(applicationLifecycle)

  useEffect(() => {
    if (application.source !== 'new') {
      setDevelopmentEntryConfirmed(true)
      return
    }
    setDevelopmentEntryConfirmed(hasApplicationEnteredDevelopment(application.id))
    return subscribeApplicationDevelopmentEntry(application.id, () => {
      setDevelopmentEntryConfirmed(true)
    })
  }, [application.id, application.source])

  // 进入开发阶段后自动异步尝试启动项目预览（首次创建和重新进入均生效）。
  // 新建应用必须同时满足模板就绪与用户已进入开发，避免在设计阶段末尾提前启动项目。
  useEffect(() => {
    const workspacePath =
      application.workspaceRoot || application.projectParentPath || ''
    if (launchCleanupTimerRef.current !== undefined) {
      window.clearTimeout(launchCleanupTimerRef.current)
      launchCleanupTimerRef.current = undefined
    }
    launchCleanupPendingRef.current = false
    if (!workspacePath) {
      activeLaunchWorkspaceRef.current = ''
      setPreviewLaunchLoading(false)
      return
    }
    // 新建应用只有进入开发阶段且模板就绪后才能启动；已有工程打开时默认已处于开发阶段。
    if (
      application.source === 'new' &&
      (!lifecycleReadyForWorkbench || !developmentEntryConfirmed)
    ) {
      activeLaunchWorkspaceRef.current = ''
      setPreviewLaunchLoading(false)
      return
    }
    activeLaunchWorkspaceRef.current = workspacePath
    if (launchedWorkspaceRef.current === workspacePath) {
      // 同一工作区的启动请求可能因 Strict Mode 重放 effect 再次进入这里；
      // 保留现有 loading，直到原请求明确成功或失败，避免按钮提前结束加载态。
      const existingLaunchRunId = launchRunIdRef.current
      return () => {
        launchCleanupPendingRef.current = true
        launchCleanupTimerRef.current = window.setTimeout(() => {
          if (
            launchRunIdRef.current === existingLaunchRunId &&
            activeLaunchWorkspaceRef.current === workspacePath
          ) {
            activeLaunchWorkspaceRef.current = ''
          }
        }, 0)
      }
    }
    const launchRunId = launchRunIdRef.current + 1
    launchRunIdRef.current = launchRunId
    launchedWorkspaceRef.current = workspacePath
    setPreviewBaseUrl('')
    setPreviewLaunchError('')
    setPreviewLaunchLoading(true)

    startProjectLaunch(workspacePath)
      .then((result) => {
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
        if (!launchStillCurrent) {
          if (result.status === 'running') {
            void stopProjectPreview(workspacePath).finally(() => {
              void window.xcodeAgent?.projectPreview?.unregisterWorkspace({
                workspaceRoot: workspacePath
              })
            })
          }
          return
        }
        setPreviewLaunchLoading(false)
        if (result.status === 'running' && result.preview_url) {
          void window.xcodeAgent?.projectPreview?.registerWorkspace({
            workspaceRoot: workspacePath
          })
          setPreviewBaseUrl(previewOrigin(result.preview_url))
          setPreviewLaunchError('')
        } else {
          const errorMsg = result.message || '未知错误'
          setPreviewBaseUrl('')
          setPreviewLaunchError(errorMsg)
        }
      })
      .catch((err) => {
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
        if (!launchStillCurrent) return
        setPreviewLaunchLoading(false)
        const errorMsg = err instanceof Error ? err.message : '网络请求失败'
        setPreviewBaseUrl('')
        setPreviewLaunchError(errorMsg)
      })
    return () => {
      launchCleanupPendingRef.current = true
      launchCleanupTimerRef.current = window.setTimeout(() => {
        if (
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath
        ) {
          activeLaunchWorkspaceRef.current = ''
        }
      }, 0)
    }
  }, [
    application.id,
    application.source,
    application.projectParentPath,
    application.workspaceRoot,
    developmentEntryConfirmed,
    lifecycleReadyForWorkbench
  ])

  useEffect(() => {
    let active = true

    // 同步可选的应用配置和规划产物；窗口重新聚焦时只校准可能被外部修改的文件。
    const syncWorkspaceFiles = async (): Promise<void> => {
      if (!application.workspaceRoot) {
        setDevelopmentPlanningPagesLoaded(true)
        return
      }
      try {
        const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot)
        if (!active) return
        setWorkspaceApplication({
          ...application,
          ...applicationConfig,
          schema: { ...application.schema, ...applicationConfig }
        })
      } catch (error) {
        console.warn('读取工作区 application.json 失败，继续使用已保存应用配置。', error)
      }
      try {
        const inspection = await inspectWorkspacePlanningArtifacts(application.workspaceRoot)
        if (!active) return
        setDevelopmentPlanningPages(inspection.pages)
        setDevelopmentPlanningPageTree(
          Array.isArray(inspection.pageTree) ? inspection.pageTree : []
        )
        setDevelopmentPlanningApiContracts(
          Array.isArray(inspection.apiContracts) ? inspection.apiContracts : []
        )
        setDevelopmentPlanningEntities(
          Array.isArray(inspection.entities) ? inspection.entities : []
        )
        setHasPageDesigns(inspection.hasPageDesigns)
        if (!inspection.ready) {
          console.warn('工作区规划产物不完整。', inspection)
        }
      } catch (error) {
        if (!active) return
        setDevelopmentPlanningPages([])
        setDevelopmentPlanningPageTree([])
        setDevelopmentPlanningApiContracts([])
        setDevelopmentPlanningEntities([])
        setHasPageDesigns(false)
        console.warn('检查 specs/plans 规划产物失败。', error)
      } finally {
        if (active) setDevelopmentPlanningPagesLoaded(true)
      }
    }

    // 首次进入由初始状态承载加载门禁；后续刷新保留当前内容，避免工作台反复清空闪烁。
    setWorkspaceApplication(application)
    void syncWorkspaceFiles()
    window.addEventListener('focus', syncWorkspaceFiles)
    return () => {
      active = false
      window.removeEventListener('focus', syncWorkspaceFiles)
    }
  }, [application, planningRefreshRevision])

  useEffect(() => {
    let active = true
    const workspaceRoot = application.workspaceRoot
    if (!workspaceRoot) return

    // 每次进入一个工作区只做一次冷启动校准；后续状态由 Workflow AG-UI 事件实时合并。
    getApplicationLifecycle({ workspaceRoot })
      .then((lifecycle) => {
        if (active) onApplicationLifecycleChange(lifecycle)
      })
      .catch((error) => {
        console.warn('读取工作台应用生命周期失败，继续使用 Workflow 实时状态。', error)
      })
    return () => {
      active = false
    }
  }, [application.id, application.workspaceRoot, onApplicationLifecycleChange])

  useEffect(() => {
    if (
      !developmentPlanningPagesLoaded ||
      !chatSessionHistoryReady ||
      entryStage !== 'loading'
    ) {
      return
    }
    const remainingVisibleTime = Math.max(
      0,
      WORKBENCH_ENTRY_MIN_VISIBLE_MS - (Date.now() - entryStartedAtRef.current)
    )
    const timer = window.setTimeout(() => setEntryStage('leaving'), remainingVisibleTime)
    return () => window.clearTimeout(timer)
  }, [chatSessionHistoryReady, developmentPlanningPagesLoaded, entryStage])

  useEffect(() => {
    if (entryStage !== 'leaving') return
    const timer = window.setTimeout(() => setEntryStage('ready'), WORKBENCH_ENTRY_FADE_MS)
    return () => window.clearTimeout(timer)
  }, [entryStage])

  const handleThemeChange = (nextTheme: Theme): void => {
    onThemeChange(nextTheme)
  }

  const handleApplicationUpdate = (updatedApplication: ApplicationConfig): void => {
    setWorkspaceApplication(updatedApplication)
  }

  // 页面或接口设计运行结束后重新读取规划目录，以持久化结果更新大纲状态。
  const handlePlanningArtifactsRefresh = (): void => {
    setPlanningRefreshRevision((current) => current + 1)
  }

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      {developmentPlanningPagesLoaded || stageWindow ? (
        <WorkbenchPhaseProvider
          applicationId={workspaceApplication.id}
          lifecycle={applicationLifecycle}
          initialPhase={initialPhase}
        >
          <div className={cx('workbench-shell-column')}>
            <WorkbenchTopBar
              application={workspaceApplication}
              workspaceRoot={
                workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
              }
              theme={theme}
              onThemeChange={handleThemeChange}
              onReturnWelcome={onReturnWelcome}
              lifecycle={applicationLifecycle}
              rightPanelOpen={rightPanelOpen}
              onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
            />
            <div className={cx('workbench-shell-body')}>
              <LeftPanel
                application={workspaceApplication}
                applicationLifecycle={applicationLifecycle}
                developmentPlanningReady={developmentPlanningPagesLoaded}
                hasPageDesigns={hasPageDesigns}
                developmentPlanningPages={developmentPlanningPages}
                developmentPlanningPageTree={developmentPlanningPageTree}
                developmentPlanningApiContracts={developmentPlanningApiContracts}
                developmentPlanningEntities={developmentPlanningEntities}
                editorMode={editorMode}
                onApplicationUpdate={handleApplicationUpdate}
                onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                previewBaseUrl={previewBaseUrl}
                previewLaunchError={previewLaunchError}
                previewLaunchLoading={previewLaunchLoading}
                onApplicationLifecycleChange={onApplicationLifecycleChange}
                onReturnWelcome={onReturnWelcome}
                onSubmitPlanningClarification={onSubmitPlanningClarification}
                onStopPlanning={onStopPlanning}
                onStartDesignStageRevision={onStartDesignStageRevision}
                onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
                onThemeChange={handleThemeChange}
                onPlanningStreamReady={onPlanningStreamReady}
                onSessionHistoryReadyChange={setChatSessionHistoryReady}
                generatingTemplate={generatingTemplate}
                planningError={planningError}
                onRetryPlanning={onRetryPlanning}
                planningThreadId={planningThreadId}
                planningConversationThreadId={planningConversationThreadId}
                planningWorkflow={planningWorkflow}
                theme={theme}
                rightPanelOpen={rightPanelOpen}
                onRightPanelOpenChange={setRightPanelOpen}
              />
            </div>
          </div>
        </WorkbenchPhaseProvider>
      ) : null}

      {entryStage !== 'ready' ? (
        <div
          aria-live="polite"
          className={cx('workbench-entry', entryStage === 'leaving' && 'is-leaving')}
          role="status"
        >
          <div className={cx('workbench-entry-glow', 'glow-one')} />
          <div className={cx('workbench-entry-glow', 'glow-two')} />
          <div className={cx('workbench-entry-content')}>
            <div className={cx('workbench-entry-mark')} aria-hidden="true">
              <span />
              <span />
            </div>
            <div className={cx('workbench-entry-kicker')}>XCODEAGENT WORKSPACE</div>
            <h1>正在进入工作台</h1>
            <p>正在同步项目配置、页面设计与历史会话</p>
            <div className={cx('workbench-entry-progress')} aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          </div>
        </div>
      ) : null}
    </Layout>
  )
}

export default WorkbenchPage

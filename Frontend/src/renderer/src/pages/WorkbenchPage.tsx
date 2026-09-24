import { Layout, message } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import { WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  isApplicationCreationComplete,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type {
  RequirementSpecDraftSaveResult,
  WorkflowRevisionContinuationHandoff
} from '../service/applicationPagePlanning'
import type { ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningAgentOption,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import { cx } from '../utils'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onEntryLoadFailure: () => void
  onReturnWelcome: () => void
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: Theme) => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  onSavePlanningRequirementSpec: (
    spec: Record<string, unknown>
  ) => Promise<RequirementSpecDraftSaveResult>
  onStopPlanning: () => Promise<void>
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 从工作台错误卡片重试设计阶段规划任务。 */
  onRetryPlanning?: () => void
  /** 通过专用动作重试失败的模板能力更新。 */
  onRetryTemplateReconcile?: () => void
  /** 当前应用唯一的 Planning 业务状态。 */
  planningState?: ApplicationPlanningCurrentState
  theme: Theme
}

type Theme = 'light' | 'dark'
type WorkbenchEntryStage = 'loading' | 'leaving' | 'ready'

const WORKBENCH_ENTRY_MIN_VISIBLE_MS = 520
const WORKBENCH_ENTRY_FADE_MS = 280
const WORKBENCH_ENTRY_TIMEOUT_MS = 15_000

// 将未知加载异常转换为可展示的工作台入口错误。
function formatWorkbenchEntryError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message.trim() : fallback
}

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({
  application,
  applicationLifecycle,
  onApplicationLifecycleChange,
  onEntryLoadFailure,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStartDesignStageRevision,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  onSavePlanningRequirementSpec,
  onStopPlanning,
  generatingTemplate,
  onRetryPlanning,
  onRetryTemplateReconcile,
  planningState,
  theme
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false)
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
  const [topologyType, setTopologyType] = useState('')
  const [developmentPlanningAgents, setDevelopmentPlanningAgents] = useState<
    DevelopmentPlanningAgentOption[]
  >([])
  const [chatSessionHistoryReady, setChatSessionHistoryReady] = useState(false)
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const entryStartedAtRef = useRef(Date.now())
  const entryStageRef = useRef<WorkbenchEntryStage>('loading')
  const entryFailureHandledRef = useRef(false)
  // 模板是否就绪（lifecycle=ready_for_workbench），用于校准正式规划产物。
  const lifecycleReadyForWorkbench = isApplicationCreationComplete(applicationLifecycle)

  // 入口失败只处理一次：提示原因、返回首页，并由顶层卸载损坏的工作台实例。
  const failWorkbenchEntry = useCallback(
    (reason: string): void => {
      if (entryStageRef.current !== 'loading' || entryFailureHandledRef.current) return
      entryFailureHandledRef.current = true
      message.error(`工作台加载失败，已返回首页：${reason}`)
      onEntryLoadFailure()
    },
    [onEntryLoadFailure]
  )

  useEffect(() => {
    entryStageRef.current = entryStage
  }, [entryStage])

  useEffect(() => {
    if (entryStage !== 'loading') return
    const timer = window.setTimeout(
      () => failWorkbenchEntry('同步项目配置、规划产物或历史会话超时。'),
      WORKBENCH_ENTRY_TIMEOUT_MS
    )
    return () => window.clearTimeout(timer)
  }, [entryStage, failWorkbenchEntry])

  useEffect(() => {
    let active = true

    // 同步可选的应用配置和规划产物；窗口重新聚焦时只校准可能被外部修改的文件。
    const syncWorkspaceFiles = async (): Promise<void> => {
      if (!application.workspaceRoot) {
        failWorkbenchEntry('应用缺少有效的工作区路径。')
        return
      }
      try {
        const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot)
        if (!active) return
        setWorkspaceApplication({
          ...application,
          ...applicationConfig
        })
      } catch (error) {
        console.warn('读取工作区 application.json 失败，终止本次工作台加载。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '读取工作区 application.json 失败。'))
        return
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
        setTopologyType(String(inspection.topologyType || ''))
        setDevelopmentPlanningAgents(Array.isArray(inspection.agents) ? inspection.agents : [])
        if (!inspection.ready) {
          console.warn('工作区规划产物不完整。', inspection)
          if (lifecycleReadyForWorkbench) {
            const details = [...inspection.missing, ...inspection.invalid].join('、')
            failWorkbenchEntry(
              details ? `工作区规划产物缺失或损坏：${details}` : '工作区规划产物不完整。'
            )
          }
        }
      } catch (error) {
        if (!active) return
        setDevelopmentPlanningPages([])
        setDevelopmentPlanningPageTree([])
        setDevelopmentPlanningApiContracts([])
        setDevelopmentPlanningEntities([])
        setDevelopmentPlanningAgents([])
        console.warn('检查 specs/plans 规划产物失败。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '检查工作区规划产物失败。'))
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
  }, [application, failWorkbenchEntry, lifecycleReadyForWorkbench, planningRefreshRevision])

  useEffect(() => {
    let active = true
    const workspaceRoot = application.workspaceRoot
    if (!workspaceRoot) return

    // 每次进入一个工作区只做一次冷启动校准；后续状态由 Workflow AG-UI 事件实时合并。
    getApplicationLifecycle({ workspaceRoot, id: application.id })
      .then((lifecycle) => {
        if (active) onApplicationLifecycleChange(lifecycle)
      })
      .catch((error) => {
        if (!active) return
        console.warn('读取工作台应用生命周期失败，终止本次工作台加载。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '读取应用生命周期失败。'))
      })
    return () => {
      active = false
    }
  }, [application.id, application.workspaceRoot, failWorkbenchEntry, onApplicationLifecycleChange])

  // 会话恢复失败时终止入口等待；普通的未就绪状态仍交给恢复流程或超时兜底。
  const handleSessionHistoryReadyChange = useCallback(
    (ready: boolean, error?: string): void => {
      setChatSessionHistoryReady(ready)
      if (error) failWorkbenchEntry(error)
    },
    [failWorkbenchEntry]
  )

  useEffect(() => {
    if (!developmentPlanningPagesLoaded || !chatSessionHistoryReady || entryStage !== 'loading') {
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
      {developmentPlanningPagesLoaded ? (
        <WorkbenchPhaseProvider
          applicationId={workspaceApplication.id}
          lifecycle={applicationLifecycle}
        >
          <div className={cx('workbench-shell-column')}>
            <WorkbenchTopBar
              application={workspaceApplication}
              workspaceRoot={
                workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
              }
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
                developmentPlanningPages={developmentPlanningPages}
                developmentPlanningPageTree={developmentPlanningPageTree}
                developmentPlanningApiContracts={developmentPlanningApiContracts}
                developmentPlanningEntities={developmentPlanningEntities}
                topologyType={topologyType}
                developmentPlanningAgents={developmentPlanningAgents}
                editorMode={editorMode}
                onApplicationUpdate={handleApplicationUpdate}
                onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                previewBaseUrl=""
                previewLaunchError=""
                previewLaunchLoading={false}
                onApplicationLifecycleChange={onApplicationLifecycleChange}
                onReturnWelcome={onReturnWelcome}
                onSubmitPlanningClarification={onSubmitPlanningClarification}
                onStartDesignStageRevision={onStartDesignStageRevision}
                onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
                onThemeChange={handleThemeChange}
                onPlanningStreamReady={onPlanningStreamReady}
                onSavePlanningRequirementSpec={onSavePlanningRequirementSpec}
                onStopPlanning={onStopPlanning}
                onSessionHistoryReadyChange={handleSessionHistoryReadyChange}
                generatingTemplate={generatingTemplate}
                onRetryPlanning={onRetryPlanning}
                onRetryTemplateReconcile={onRetryTemplateReconcile}
                planningState={planningState}
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
            <div className={cx('workbench-entry-kicker')}>DevAgent Studio WORKSPACE</div>
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

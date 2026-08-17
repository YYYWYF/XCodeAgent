import { Layout, notification } from 'antd'
import { LoadingOutlined } from '@ant-design/icons'
import { useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import { WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  isApplicationCreationComplete,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../typings'
import { cx, previewOrigin } from '../utils'
import { startProjectLaunch, stopProjectPreview } from '../service/projectLaunch'
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
    requirementSpecFeedback?: string
  ) => void
  onThemeChange: (theme: Theme) => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  /** 模板生成失败后重试（重新触发模板生成）。 */
  onRetryTemplate?: () => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  planningThreadId?: string
  planningWorkflow?: WorkflowRunPayload
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
  onThemeChange,
  onPlanningStreamReady,
  onRetryTemplate,
  generatingTemplate,
  planningThreadId,
  planningWorkflow,
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
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  const [previewBaseUrl, setPreviewBaseUrl] = useState('')
  const [previewLaunchError, setPreviewLaunchError] = useState('')
  // 预览启动中状态：驱动左侧上下文头"预览页面"按钮的 loading 呈现（合并 dev_agent 的轻量化改动补齐）。
  const [previewLaunchLoading, setPreviewLaunchLoading] = useState(false)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const entryStartedAtRef = useRef(Date.now())
  const launchedWorkspaceRef = useRef<string>()
  const activeLaunchWorkspaceRef = useRef('')
  const launchRunIdRef = useRef(0)
  const launchCleanupPendingRef = useRef(false)
  const launchCleanupTimerRef = useRef<number>()
  // 模板是否就绪（lifecycle=ready_for_workbench）。用 boolean 而非整个 lifecycle 作为预览启动
  // effect 的依赖，避免规划期流式 workflow 事件频繁递增 revision 导致 effect 反复 cleanup，
  // 进而中断正在进行的 npm install / dev server 启动。
  const lifecycleReadyForWorkbench = isApplicationCreationComplete(applicationLifecycle)

  // 进入工作台时自动异步尝试启动项目预览（首次创建和重新进入均生效）。
  // 新建应用需等模板拉取完成（lifecycle=ready_for_workbench）后才有 frontend/package.json，
  // 在此之前启动会报"未找到前端 package.json"，故规划期跳过，就绪后再启动。
  useEffect(() => {
    const workspacePath =
      application.workspaceRoot || application.projectParentPath || ''
    console.log('[preview-launch-effect]', 'workspace=', workspacePath, 'source=', application.source, 'ready=', lifecycleReadyForWorkbench, 'launched=', launchedWorkspaceRef.current, 'activeLaunch=', activeLaunchWorkspaceRef.current)
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
    // 新建应用在模板就绪前不启动预览（工作区尚无 frontend/package.json）。
    // 非新建应用（source !== 'new'）已有完整工程，直接启动。
    if (application.source === 'new' && !lifecycleReadyForWorkbench) {
      activeLaunchWorkspaceRef.current = ''
      return
    }
    activeLaunchWorkspaceRef.current = workspacePath
    if (launchedWorkspaceRef.current === workspacePath) {
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

    const loadingKey = `project-launch-${application.id}-${launchRunId}`
    notification.open({
      key: loadingKey,
      message: '项目正在启动中',
      description: '正在安装依赖并启动开发服务器，请稍候...',
      placement: 'bottomRight',
      duration: null,
      icon: <LoadingOutlined />,
      className: cx('project-launch-loading'),
    })
    setPreviewLaunchLoading(true)

    startProjectLaunch(workspacePath).then(result => {
      const launchStillCurrent =
        launchRunIdRef.current === launchRunId &&
        activeLaunchWorkspaceRef.current === workspacePath &&
        !launchCleanupPendingRef.current
      console.log('[preview-launch-result]', 'status=', result.status, 'previewUrl=', result.preview_url, 'stillCurrent=', launchStillCurrent, 'cleanupPending=', launchCleanupPendingRef.current, 'msg=', result.message)
      notification.close(loadingKey)
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
      if (result.status === 'running' && result.preview_url) {
        void window.xcodeAgent?.projectPreview?.registerWorkspace({ workspaceRoot: workspacePath })
        setPreviewBaseUrl(previewOrigin(result.preview_url))
        setPreviewLaunchError('')
        setPreviewLaunchLoading(false)
        notification.success({
          message: '项目预览已启动',
          description: '可在预览面板中查看效果',
          placement: 'bottomRight',
          duration: 3,
        })
      } else {
        const errorMsg = result.message || '未知错误'
        setPreviewBaseUrl('')
        setPreviewLaunchError(errorMsg)
        setPreviewLaunchLoading(false)
        notification.warning({
          message: '项目预览启动失败',
          description: `${errorMsg}，可在预览区查看详情`,
          placement: 'bottomRight',
          duration: 3,
        })
      }
    }).catch(err => {
      notification.close(loadingKey)
      const launchStillCurrent =
        launchRunIdRef.current === launchRunId &&
        activeLaunchWorkspaceRef.current === workspacePath &&
        !launchCleanupPendingRef.current
      if (!launchStillCurrent) return
      const errorMsg = err instanceof Error ? err.message : '网络请求失败'
      setPreviewBaseUrl('')
      setPreviewLaunchError(errorMsg)
      setPreviewLaunchLoading(false)
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
  }, [application.id, application.source, application.projectParentPath, application.workspaceRoot, lifecycleReadyForWorkbench])

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
        setDevelopmentPlanningPageTree(Array.isArray(inspection.pageTree) ? inspection.pageTree : [])
        setDevelopmentPlanningApiContracts(
          Array.isArray(inspection.apiContracts) ? inspection.apiContracts : []
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
    if (!developmentPlanningPagesLoaded || entryStage !== 'loading') return
    const remainingVisibleTime = Math.max(
      0,
      WORKBENCH_ENTRY_MIN_VISIBLE_MS - (Date.now() - entryStartedAtRef.current)
    )
    const timer = window.setTimeout(() => setEntryStage('leaving'), remainingVisibleTime)
    return () => window.clearTimeout(timer)
  }, [developmentPlanningPagesLoaded, entryStage])

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
                editorMode={editorMode}
                onApplicationUpdate={handleApplicationUpdate}
                onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                previewBaseUrl={previewBaseUrl}
                previewLaunchError={previewLaunchError}
                previewLaunchLoading={previewLaunchLoading}
                onApplicationLifecycleChange={onApplicationLifecycleChange}
                onReturnWelcome={onReturnWelcome}
                onSubmitPlanningClarification={onSubmitPlanningClarification}
                onThemeChange={handleThemeChange}
                onPlanningStreamReady={onPlanningStreamReady}
                onRetryTemplate={onRetryTemplate}
                generatingTemplate={generatingTemplate}
                planningThreadId={planningThreadId}
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
            <p>正在同步项目配置与页面设计状态</p>
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

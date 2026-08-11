import { Layout } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { LeftPanel } from '../components'
import {
  inspectWorkspacePlanningArtifacts,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../typings'
import { cx, previewOrigin } from '../utils'
import { startProjectLaunch, stopProjectPreview } from '../service/projectLaunch'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onReturnWelcome: () => void
  onThemeChange: (theme: Theme) => void
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
  onThemeChange,
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
  const [previewLaunchLoading, setPreviewLaunchLoading] = useState(false)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const entryStartedAtRef = useRef(Date.now())
  const launchedWorkspaceRef = useRef<string>()
  const activeLaunchWorkspaceRef = useRef('')
  const launchRunIdRef = useRef(0)
  const launchCleanupPendingRef = useRef(false)
  const launchCleanupTimerRef = useRef<number>()

  // 进入工作台时自动异步尝试启动项目预览（首次创建和重新进入均生效）
  useEffect(() => {
    const workspacePath = application.workspaceRoot || application.projectParentPath || ''
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
  }, [application.id, application.projectParentPath, application.workspaceRoot])

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
          onThemeChange={handleThemeChange}
          theme={theme}
        />
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

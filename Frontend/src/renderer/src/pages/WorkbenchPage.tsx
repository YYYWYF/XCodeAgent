import { Layout } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { LeftPanel } from '../components'
import {
  inspectWorkspacePlanningArtifacts,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationPagePlanning'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../typings'
import { cx } from '../utils'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onReturnWelcome: () => void
}

type Theme = 'light' | 'dark'
type WorkbenchEntryStage = 'loading' | 'leaving' | 'ready'

const THEME_PREFERENCE_KEY = 'xcode-agent-theme-preference'
const WORKBENCH_ENTRY_MIN_VISIBLE_MS = 520
const WORKBENCH_ENTRY_FADE_MS = 280

function getTheme(): Theme {
  const storedPreference = window.localStorage.getItem(THEME_PREFERENCE_KEY)
  return storedPreference === 'light' || storedPreference === 'dark' ? storedPreference : 'light'
}

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({
  application,
  applicationLifecycle,
  onApplicationLifecycleChange,
  onReturnWelcome
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [theme, setTheme] = useState<Theme>(getTheme)
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false)
  const [hasPageDesigns, setHasPageDesigns] = useState(false)
  const [developmentPlanningPages, setDevelopmentPlanningPages] = useState<
    DevelopmentPlanningPageOption[]
  >([])
  const [developmentPlanningApiContracts, setDevelopmentPlanningApiContracts] = useState<
    DevelopmentPlanningApiContract[]
  >([])
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const entryStartedAtRef = useRef(Date.now())

  useEffect(() => {
    let active = true

    // 同步可选的应用配置，并独立读取规划产物及其中的页面清单。
    const syncWorkspaceApplication = async (): Promise<void> => {
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
        setDevelopmentPlanningApiContracts([])
        setHasPageDesigns(false)
        console.warn('检查 specs/plans 规划产物失败。', error)
      } finally {
        if (active) setDevelopmentPlanningPagesLoaded(true)
      }
      try {
        const lifecycle = await getApplicationLifecycle(application)
        if (active) onApplicationLifecycleChange(lifecycle)
      } catch (error) {
        console.warn('读取工作台应用生命周期失败，继续使用 Workflow 实时状态。', error)
      }
    }

    // 首次进入由初始状态承载加载门禁；后续刷新保留当前内容，避免工作台反复清空闪烁。
    setWorkspaceApplication(application)
    void syncWorkspaceApplication()
    window.addEventListener('focus', syncWorkspaceApplication)
    return () => {
      active = false
      window.removeEventListener('focus', syncWorkspaceApplication)
    }
  }, [application, onApplicationLifecycleChange, planningRefreshRevision])

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
    setTheme(nextTheme)
    window.localStorage.setItem(THEME_PREFERENCE_KEY, nextTheme)
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
          developmentPlanningApiContracts={developmentPlanningApiContracts}
          editorMode={editorMode}
          onApplicationUpdate={handleApplicationUpdate}
          onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
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

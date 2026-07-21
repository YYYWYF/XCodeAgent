import { useCallback, useEffect, useState } from 'react'
import { message } from 'antd'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import {
  clearActiveApplicationPlanning,
  isActiveApplicationPlanningIndexed,
  isApplicationPlanningConfirmed,
  loadActiveApplicationPlanning,
  recoverActiveApplicationPlanning,
  saveActiveApplicationPlanning,
  type ActivePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import {
  APPLICATIONS_CHANGED_EVENT,
  canOpenApplicationWorkbench
} from '../service/applicationStorage'
import { saveApplication } from '../components/Welcome/applicationService'
import { generatePageFiles } from '../service/templateApi'
import type {
  ApplicationConfig,
  ApplicationPlanningConfirmation,
  WorkflowRunPayload
} from '../typings'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'

// 读取规划页需要沿用的欢迎页主题。
function getEntryTheme(): 'dark' | 'light' {
  return window.localStorage.getItem('xcode-agent-theme-preference') === 'dark' ? 'dark' : 'light'
}

// 在欢迎页、全屏规划页与应用工作台之间维护顶层导航和长时规划会话。
export default function AppEntryPage() {
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activePlanning, setActivePlanning] = useState<PersistedActivePlanning | undefined>(
    loadActiveApplicationPlanning
  )
  const [planningVisible, setPlanningVisible] = useState(false)

  // 规划状态或快照变化后立即落盘，保证刷新和客户端重启后仍可恢复。
  useEffect(() => {
    if (activePlanning) saveActiveApplicationPlanning(activePlanning)
  }, [activePlanning])

  // 启动及应用索引变化时校验活动规划，避免已删除应用继续显示旧确认文档。
  useEffect(() => {
    let disposed = false
    let refreshId = 0

    const reconcileActivePlanning = async (): Promise<void> => {
      const currentRefreshId = ++refreshId
      const persisted = loadActiveApplicationPlanning()
      if (persisted && (await isActiveApplicationPlanningIndexed(persisted))) return
      if (persisted) clearActiveApplicationPlanning(persisted.threadId)

      const recovered = await recoverActiveApplicationPlanning()
      if (disposed || currentRefreshId !== refreshId) return
      setActivePlanning(recovered)
      if (!recovered) return
      saveActiveApplicationPlanning(recovered)
      void saveApplication(recovered.application)
    }

    const handleApplicationsChanged = (): void => {
      void reconcileActivePlanning()
    }

    void reconcileActivePlanning()
    window.addEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    return () => {
      disposed = true
      window.removeEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    }
  }, [])

  // 清理修复前已经确认但仍残留在当前页面状态中的规划入口。
  useEffect(() => {
    if (!activePlanning || !isApplicationPlanningConfirmed(activePlanning)) return
    const confirmedApplication = {
      ...activePlanning.application,
      planningConfirmedAt: activePlanning.application.planningConfirmedAt || Date.now()
    }
    clearActiveApplicationPlanning(activePlanning.threadId)
    setActivePlanning(undefined)
    void saveApplication(confirmedApplication)
  }, [])

  // 从工作台返回欢迎页。
  const handleReturnWelcome = () => {
    setActiveApplication(null)
  }

  // 启动新的独立规划会话并展示全屏生成页。
  const handleStartPlanning = (application: ApplicationConfig, threadId: string): void => {
    setActivePlanning({ application, status: 'running', threadId })
    setPlanningVisible(true)
  }

  // 仅允许最终规划已确认的创建项目进入工作台，未确认项目继续回到原规划会话。
  const handleOpenApplication = useCallback(
    (application: ApplicationConfig): void => {
      if (activePlanning?.application.id === application.id) {
        setPlanningVisible(true)
        return
      }
      if (canOpenApplicationWorkbench(application)) {
        setActiveApplication(application)
        return
      }
      message.info('请先完成并确认应用计划')
    },
    [activePlanning]
  )

  // 接收规划页状态，并避免相同状态造成无意义的首页重渲染。
  const handlePlanningStatusChange = useCallback((status: ActivePlanningStatus): void => {
    setActivePlanning((current) => {
      if (!current || current.status === status) return current
      return { ...current, status }
    })
  }, [])

  // 保存规划页收到的最新公开 Workflow 快照，供重新进入时恢复确认界面。
  const handlePlanningWorkflowChange = useCallback((workflow: WorkflowRunPayload): void => {
    setActivePlanning((current) => (current ? { ...current, workflow } : current))
  }, [])

  // 在 RequirementSpec 与 ProjectPlan 均确认后结束规划入口并打开工作台。
  // 进入工作台前，先把规划产出的页面追加到模板工程 apps/<应用名>/frontend/src/pages/ 下。
  const handlePlanningConfirmed = async (
    _confirmation: ApplicationPlanningConfirmation
  ): Promise<void> => {
    if (!activePlanning) return
    const confirmedApplication = {
      ...activePlanning.application,
      planningConfirmedAt: Date.now()
    }
    await saveApplication(confirmedApplication)

    // 生成页面占位文件（hello agent!），失败不阻塞进入工作台
    try {
      const projectPath =
        confirmedApplication.workspaceRoot ||
        confirmedApplication.projectParentPath ||
        ''
      const result = await generatePageFiles(
        confirmedApplication.schema,
        projectPath,
        activePlanning.workflow
      )
      if (result.written.length > 0) {
        message.success(`已生成 ${result.written.length} 个页面文件，正在进入工作台`)
      }
    } catch (reason) {
      console.error('[页面文件生成失败]', reason)
      message.warning('页面文件生成失败，可在工作台中重试')
    }

    clearActiveApplicationPlanning(activePlanning.threadId)
    setActivePlanning(undefined)
    setPlanningVisible(false)
    setActiveApplication(confirmedApplication)
  }

  if (!activeApplication) {
    return (
      <>
        <div hidden={planningVisible}>
          <WelcomePage
            activePlanning={activePlanning?.application}
            activePlanningStatus={activePlanning?.status}
            activePlanningWorkflow={activePlanning?.workflow}
            onOpenApplication={handleOpenApplication}
            onOpenPlanning={() => setPlanningVisible(true)}
            onStartPlanning={handleStartPlanning}
          />
        </div>
        {activePlanning ? (
          <ApplicationPagePlanningModal
            application={activePlanning.application}
            initialStatus={activePlanning.status}
            initialWorkflow={activePlanning.workflow}
            key={activePlanning.threadId}
            onConfirmed={handlePlanningConfirmed}
            onReturnHome={() => setPlanningVisible(false)}
            onStatusChange={handlePlanningStatusChange}
            onWorkflowChange={handlePlanningWorkflowChange}
            theme={getEntryTheme()}
            threadId={activePlanning.threadId}
            visible={planningVisible}
          />
        ) : null}
      </>
    )
  }

  return (
    <WorkbenchPage
      application={activeApplication}
      onReturnWelcome={handleReturnWelcome}
    />
  )
}

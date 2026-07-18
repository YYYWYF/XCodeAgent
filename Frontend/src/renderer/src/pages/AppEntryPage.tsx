import { useCallback, useEffect, useState } from 'react'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import {
  clearActiveApplicationPlanning,
  isApplicationPlanningConfirmed,
  loadActiveApplicationPlanning,
  recoverActiveApplicationPlanning,
  saveActiveApplicationPlanning,
  type ActivePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import { saveApplication } from '../components/Welcome/applicationService'
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

  // 兼容旧版本丢失的内存状态，从持久化应用索引恢复最近的规划线程。
  useEffect(() => {
    if (activePlanning) return
    let disposed = false
    void recoverActiveApplicationPlanning().then((recovered) => {
      if (disposed || !recovered) return
      setActivePlanning(recovered)
      void saveApplication(recovered.application)
    })
    return () => {
      disposed = true
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
  const handlePlanningConfirmed = async (
    _confirmation: ApplicationPlanningConfirmation
  ): Promise<void> => {
    if (!activePlanning) return
    const confirmedApplication = {
      ...activePlanning.application,
      planningConfirmedAt: Date.now()
    }
    await saveApplication(confirmedApplication)
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
            onOpenApplication={setActiveApplication}
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

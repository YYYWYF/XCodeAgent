import { useCallback, useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import {
  activePlanningStatus,
  loadActiveApplicationPlanning,
  workflowApplicationLifecycle,
  type ActivePlanningStatus,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import {
  completeApplicationTemplateGeneration,
  getApplicationLifecycle
} from '../service/applicationPagePlanning'
import {
  APPLICATIONS_CHANGED_EVENT,
  canOpenApplicationWorkbench
} from '../service/applicationStorage'
import { saveApplication } from '../components/Welcome/applicationService'
import {
  generateApplicationTemplateFiles as writeApplicationTemplateFiles
} from '../service/templateApi'
import type {
  ApplicationConfig,
  ApplicationPlanningConfirmation,
  ApplicationLifecycle,
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
  const [activePlanning, setActivePlanning] = useState<PersistedActivePlanning | undefined>()
  const [planningVisible, setPlanningVisible] = useState(false)
  const templateGenerationRunRef = useRef<string>()

  // 启动及应用索引变化时读取活动规划，避免已删除应用继续显示确认文档。
  useEffect(() => {
    let disposed = false
    let refreshId = 0

    const refreshActivePlanning = async (): Promise<void> => {
      const currentRefreshId = ++refreshId
      const recovered = await loadActiveApplicationPlanning()
      if (disposed || currentRefreshId !== refreshId) return
      setActivePlanning(recovered)
    }

    const handleApplicationsChanged = (): void => {
      void refreshActivePlanning()
    }

    void refreshActivePlanning()
    window.addEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    return () => {
      disposed = true
      window.removeEventListener(APPLICATIONS_CHANGED_EVENT, handleApplicationsChanged)
    }
  }, [])

  // 从工作台返回欢迎页。
  const handleReturnWelcome = () => {
    setActiveApplication(null)
  }

  // 启动新的独立规划会话并展示全屏生成页。
  const handleStartPlanning = (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ): void => {
    setActivePlanning({
      application,
      lifecycle,
      status: 'running',
      threadId
    })
    setPlanningVisible(true)
  }

  // 仅允许最终规划已确认的创建项目进入工作台，未确认项目继续回到原规划会话。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      if (activePlanning?.application.id === application.id) {
        setPlanningVisible(true)
        return
      }
      if (application.source !== 'new' && canOpenApplicationWorkbench(application)) {
        setActiveApplication(application)
        return
      }
      try {
        const lifecycle = await getApplicationLifecycle(application)
        if (canOpenApplicationWorkbench(application, lifecycle)) {
          setActiveApplication(application)
          return
        }
      } catch (error) {
        console.warn('读取应用生命周期失败', error)
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
    setActivePlanning((current) => {
      if (!current) return current
      const lifecycle = workflowApplicationLifecycle(workflow) || current.lifecycle
      return {
        ...current,
        lifecycle,
        workflow
      }
    })
  }, [])

  // 生成应用模板文件并通过 AG-UI 把结果提交给后端生命周期状态机。
  const generateApplicationTemplateFiles = useCallback(async (
    planning: PersistedActivePlanning
  ): Promise<void> => {
    const runKey = planning.application.id
    if (templateGenerationRunRef.current === runKey) return
    templateGenerationRunRef.current = runKey
    let failureMessage = ''
    try {
      const projectPath =
        planning.application.workspaceRoot || planning.application.projectParentPath || ''
      const result = await writeApplicationTemplateFiles(
        planning.application.schema,
        projectPath,
        planning.workflow
      )
      if (result.written.length > 0) {
        message.success(`已生成 ${result.written.length} 个应用模板文件`)
      }
    } catch (reason) {
      console.error('[应用模板文件生成失败]', reason)
      failureMessage = reason instanceof Error ? reason.message : String(reason)
    }

    try {
      const lifecycle = await completeApplicationTemplateGeneration(
        planning.application,
        planning.threadId,
        !failureMessage,
        failureMessage || undefined
      )
      if (lifecycle.lifecycle.stage === 'ready_for_workbench') {
        await saveApplication(planning.application)
        setActivePlanning(undefined)
        setPlanningVisible(false)
        setActiveApplication(planning.application)
        message.success('应用模板文件生成完成，正在进入工作台')
        return
      }
      setActivePlanning({
        ...planning,
        lifecycle,
        status: activePlanningStatus(lifecycle)
      })
      message.error(lifecycle.error?.message || '应用模板文件生成失败')
    } finally {
      templateGenerationRunRef.current = undefined
    }
  }, [])

  // 服务重启后若停在模板文件生成阶段，自动以同一幂等动作继续。
  useEffect(() => {
    if (
      planningVisible ||
      activePlanning?.lifecycle.lifecycle.stage !== 'generating_application_template_files'
    ) return
    void generateApplicationTemplateFiles(activePlanning)
  }, [activePlanning, generateApplicationTemplateFiles, planningVisible])

  // 在 RequirementSpec 与 ProjectPlan 均确认后结束规划入口并打开工作台。
  // 进入工作台前，先把规划产出的页面追加到模板工程 apps/<应用名>/frontend/src/pages/ 下。
  const handlePlanningConfirmed = async (
    _confirmation: ApplicationPlanningConfirmation
  ): Promise<void> => {
    if (!activePlanning) return
    await generateApplicationTemplateFiles(activePlanning)
  }

  if (!activeApplication) {
    return (
      <>
        <div hidden={planningVisible}>
          <WelcomePage
            activePlanning={activePlanning?.application}
            activePlanningStatus={activePlanning?.status}
            activePlanningLifecycle={activePlanning?.lifecycle}
            onOpenApplication={handleOpenApplication}
            onOpenPlanning={() => {
              if (
                activePlanning?.lifecycle.lifecycle.stage ===
                'application_template_generation_failed'
              ) {
                void generateApplicationTemplateFiles(activePlanning)
                return
              }
              setPlanningVisible(true)
            }}
            onStartPlanning={handleStartPlanning}
          />
        </div>
        {activePlanning ? (
          <ApplicationPagePlanningModal
            application={activePlanning.application}
            initialLifecycle={activePlanning.lifecycle}
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

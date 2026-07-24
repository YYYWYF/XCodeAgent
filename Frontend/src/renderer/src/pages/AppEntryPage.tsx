import { useCallback, useEffect, useRef, useState } from 'react'
import { message, Modal } from 'antd'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
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
} from '../service/applicationLifecycle'
import {
  APPLICATIONS_CHANGED_EVENT,
  canOpenApplicationWorkbench,
  deleteStoredAgentDirectory,
  removeStoredApplication
} from '../service/applicationStorage'
import { saveApplication } from '../components/Welcome/applicationService'
import {
  fetchTemplateCode,
  generateApplicationTemplateFiles as writeApplicationTemplateFiles
} from '../service/templateApi'
import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { cx } from '../utils'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'

type ActiveSurface = 'welcome' | 'workbench'

// 读取规划页需要沿用的欢迎页主题。
function getEntryTheme(): 'dark' | 'light' {
  return window.localStorage.getItem('xcode-agent-theme-preference') === 'dark' ? 'dark' : 'light'
}

/** 在应用根部持有不会随工作台显隐而销毁的会话运行管理器。 */
export default function AppEntryPage(): JSX.Element {
  return (
    <SessionRuntimeProvider>
      <AppEntryContent />
    </SessionRuntimeProvider>
  )
}

// 在欢迎页、全屏规划页与应用工作台之间维护顶层导航和长时规划会话。
function AppEntryContent(): JSX.Element {
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activeSurface, setActiveSurface] = useState<ActiveSurface>('welcome')
  const [activePlanning, setActivePlanning] = useState<PersistedActivePlanning | undefined>()
  const [planningVisible, setPlanningVisible] = useState(false)
  const [deletingActivePlanning, setDeletingActivePlanning] = useState(false)
  const templateGenerationRunRef = useRef<string>()
  const templateGenerationTaskRef = useRef<Promise<boolean>>()
  const planningStopHandlerRef = useRef<() => Promise<void>>()
  const { lifecycle: applicationLifecycle, mergeLifecycle: mergeApplicationLifecycle } =
    useApplicationLifecycleStore(activeApplication?.id || '')

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

  // 从工作台直接返回欢迎页，后台任务由保持挂载的工作台继续运行。
  const handleReturnWelcome = (): void => {
    setActiveSurface('welcome')
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

  // 应用计划一旦确认便永久放行工作台；否则读取当前 lifecycle 判断初始化是否完成。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      if (canOpenApplicationWorkbench(application)) {
        if (activePlanning?.application.id === application.id) {
          setActivePlanning(undefined)
          setPlanningVisible(false)
        }
        setActiveApplication(application)
        setActiveSurface('workbench')
        return
      }
      try {
        const lifecycle = await getApplicationLifecycle(application)
        if (canOpenApplicationWorkbench(application, lifecycle)) {
          const confirmedApplication = application.planningConfirmedAt
            ? application
            : { ...application, planningConfirmedAt: Date.now() }
          if (confirmedApplication !== application) {
            await saveApplication(confirmedApplication)
          }
          if (activePlanning?.application.id === application.id) {
            setActivePlanning(undefined)
            setPlanningVisible(false)
          }
          setActiveApplication(confirmedApplication)
          mergeApplicationLifecycle(lifecycle)
          setActiveSurface('workbench')
          return
        }
      } catch (error) {
        console.warn('读取应用生命周期失败', error)
      }
      if (activePlanning?.application.id === application.id) {
        setPlanningVisible(true)
        return
      }
      message.info('请先完成并确认应用计划')
    },
    [activePlanning, mergeApplicationLifecycle]
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

  // 保存全屏规划页当前会话的停止句柄，供首页外层控制调用。
  const handlePlanningStopHandlerChange = useCallback((handler?: () => Promise<void>): void => {
    planningStopHandlerRef.current = handler
  }, [])

  // 生成应用模板文件并通过 AG-UI 把结果提交给后端生命周期状态机。
  const generateApplicationTemplateFiles = useCallback(
    (planning: PersistedActivePlanning): Promise<boolean> => {
      const runKey = planning.application.id
      if (templateGenerationRunRef.current === runKey && templateGenerationTaskRef.current) {
        return templateGenerationTaskRef.current
      }
      templateGenerationRunRef.current = runKey
      const task = (async (): Promise<boolean> => {
        let failureMessage = ''
        const projectPath =
          planning.application.workspaceRoot || planning.application.projectParentPath || ''

        // 模板拉取失败沿用既有非阻断语义；本地正式文件写入失败才阻止 lifecycle 放行。
        try {
          await fetchTemplateCode(planning.application.schema, projectPath)
        } catch (templateError) {
          console.error('[模板拉取失败]', templateError)
          message.warning('模板拉取失败，可在工作台中重试')
        }

        try {
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

        const lifecycle = await completeApplicationTemplateGeneration(
          planning.application,
          planning.threadId,
          !failureMessage,
          failureMessage || undefined
        )
        if (!canOpenApplicationWorkbench(planning.application, lifecycle)) {
          setActivePlanning({
            ...planning,
            lifecycle,
            status: activePlanningStatus(lifecycle)
          })
          message.error(lifecycle.error?.message || '应用模板文件生成失败')
          return false
        }

        const confirmedApplication = { ...planning.application, planningConfirmedAt: Date.now() }
        await saveApplication(confirmedApplication)
        setActivePlanning(undefined)
        setPlanningVisible(false)
        setActiveApplication(confirmedApplication)
        mergeApplicationLifecycle(lifecycle)
        setActiveSurface('workbench')
        message.success('应用模板文件生成完成，正在进入工作台')
        return true
      })()
      templateGenerationTaskRef.current = task
      void task.then(
        () => {
          if (templateGenerationTaskRef.current === task) {
            templateGenerationTaskRef.current = undefined
            templateGenerationRunRef.current = undefined
          }
        },
        () => {
          if (templateGenerationTaskRef.current === task) {
            templateGenerationTaskRef.current = undefined
            templateGenerationRunRef.current = undefined
          }
        }
      )
      return task
    },
    [mergeApplicationLifecycle]
  )

  // 停止初始化运行，等待可能的模板写入退出，再删除应用索引和 .xcodeagent 目录。
  const deleteActivePlanning = useCallback(async (): Promise<void> => {
    const planning = activePlanning
    const workspaceRoot = planning?.application.workspaceRoot
    if (!planning || !workspaceRoot || deletingActivePlanning) return
    setDeletingActivePlanning(true)
    try {
      await planningStopHandlerRef.current?.()
      await templateGenerationTaskRef.current?.catch(() => undefined)
      await deleteStoredAgentDirectory(workspaceRoot)
      await removeStoredApplication(planning.application.id)
      setActivePlanning(undefined)
      setPlanningVisible(false)
      setActiveApplication(null)
      setActiveSurface('welcome')
      message.success('初始化计划和 .xcodeagent 目录已删除')
    } catch (reason) {
      const errorMessage = reason instanceof Error ? reason.message : String(reason)
      message.error(`删除初始化计划失败：${errorMessage}`)
    } finally {
      setDeletingActivePlanning(false)
    }
  }, [activePlanning, deletingActivePlanning])

  // 二次确认停止与本地目录清理，避免误删尚未确认的规划文档。
  const confirmDeleteActivePlanning = useCallback((): void => {
    if (!activePlanning || deletingActivePlanning) return
    Modal.confirm({
      title: `停止并删除「${activePlanning.application.appName}」的初始化计划？`,
      content: '正在执行的规划会被停止，生成的 .xcodeagent 目录及其中的规划文档会被永久删除。',
      okText: '确认',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: deleteActivePlanning,
      wrapClassName: cx('welcome-modal', `theme-${getEntryTheme()}`)
    })
  }, [activePlanning, deleteActivePlanning, deletingActivePlanning])

  // 服务重启后若停在模板文件生成阶段，自动以同一幂等动作继续。
  useEffect(() => {
    if (
      planningVisible ||
      activePlanning?.lifecycle.initialization.stage !== 'generating_application_template_files'
    )
      return
    void generateApplicationTemplateFiles(activePlanning)
  }, [activePlanning, generateApplicationTemplateFiles, planningVisible])

  // 在 RequirementSpec 与 ProjectPlan 均确认后结束规划入口并打开工作台。
  // 进入工作台前，先拉取模板工程代码，再把规划产出的页面追加到 frontend/src/pages/ 下。
  const handlePlanningConfirmed = async (): Promise<boolean> => {
    if (!activePlanning) return false
    return generateApplicationTemplateFiles(activePlanning)
  }

  return (
    <>
      <div
        aria-hidden={activeSurface !== 'welcome' || planningVisible}
        hidden={activeSurface !== 'welcome' || planningVisible}
      >
        <WelcomePage
          activePlanning={activePlanning?.application}
          activePlanningStatus={activePlanning?.status}
          activePlanningLifecycle={activePlanning?.lifecycle}
          deletingActivePlanning={deletingActivePlanning}
          onDeletePlanning={confirmDeleteActivePlanning}
          onOpenApplication={handleOpenApplication}
          onOpenPlanning={() => {
            if (
              activePlanning?.lifecycle.initialization.stage ===
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
          onStopHandlerChange={handlePlanningStopHandlerChange}
          onWorkflowChange={handlePlanningWorkflowChange}
          theme={getEntryTheme()}
          threadId={activePlanning.threadId}
          visible={planningVisible}
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
            onReturnWelcome={handleReturnWelcome}
          />
        </div>
      ) : null}
    </>
  )
}

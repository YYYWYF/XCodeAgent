import { useCallback, useRef, useState } from 'react'
import { message } from 'antd'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import { saveApplication } from '../components/Welcome/applicationService'
import ApplicationPagePlanningModal from '../components/Welcome/ApplicationPagePlanningModal'
import { useActiveApplicationPlannings } from '../hooks/useActiveApplicationPlannings'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
import { useApplicationTheme } from '../hooks/useApplicationTheme'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { canOpenApplicationWorkbench } from '../service/applicationStorage'
import { stopProjectPreview } from '../service/projectLaunch'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import WelcomePage from './WelcomePage'
import WorkbenchPage from './WorkbenchPage'

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

// 在欢迎页、多个全屏规划会话与应用工作台之间维护顶层导航。
function AppEntryContent(): JSX.Element {
  const { theme, setTheme } = useApplicationTheme()
  const [activeApplication, setActiveApplication] = useState<ApplicationConfig | null>(null)
  const [activeSurface, setActiveSurface] = useState<ActiveSurface>('welcome')
  const activePreviewWorkspaceRef = useRef('')
  const { lifecycle: applicationLifecycle, mergeLifecycle: mergeApplicationLifecycle } =
    useApplicationLifecycleStore(activeApplication?.id || '')

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
    onOpenWorkbench: openWorkbench,
    theme
  })

  // 从工作台直接返回欢迎页，后台任务由保持挂载的工作台和规划页继续运行。
  const handleReturnWelcome = (): void => {
    setActiveSurface('welcome')
  }

  // 已完成应用直接进入工作台；未完成应用只打开其对应的独立规划会话。
  const handleOpenApplication = useCallback(
    async (application: ApplicationConfig): Promise<void> => {
      if (canOpenApplicationWorkbench(application)) {
        planningController.dismissPlanning(application.id)
        await openWorkbench(application)
        return
      }
      try {
        const lifecycle = await getApplicationLifecycle(application)
        if (canOpenApplicationWorkbench(application, lifecycle)) {
          const confirmedApplication = application.planningConfirmedAt
            ? application
            : { ...application, planningConfirmedAt: Date.now() }
          const persistedApplication =
            confirmedApplication !== application
              ? await saveApplication(confirmedApplication)
              : confirmedApplication
          planningController.dismissPlanning(application.id)
          await openWorkbench(persistedApplication, lifecycle)
          return
        }
      } catch (error) {
        console.warn('读取应用生命周期失败', error)
      }
      const hasActivePlanning = planningController.activePlannings.some(
        (planning) => planning.application.id === application.id
      )
      if (hasActivePlanning) {
        planningController.showPlanning(application.id)
        return
      }
      message.info('请先完成并确认应用计划')
    },
    [openWorkbench, planningController]
  )

  const planningVisible = Boolean(planningController.visiblePlanningId)

  return (
    <>
      <div
        aria-hidden={activeSurface !== 'welcome' || planningVisible}
        hidden={activeSurface !== 'welcome' || planningVisible}
      >
        <WelcomePage
          activePlannings={planningController.activePlannings}
          deletingPlanningIds={planningController.deletingPlanningIds}
          onDeletePlanning={planningController.removePlanning}
          onOpenApplication={handleOpenApplication}
          onOpenPlanning={planningController.showPlanning}
          onStartPlanning={planningController.startPlanning}
          theme={theme}
        />
      </div>

      {planningController.activePlannings.map((planning) => (
        <ApplicationPagePlanningModal
          application={planning.application}
          initialLifecycle={planning.lifecycle}
          initialStatus={planning.status}
          initialWorkflow={planning.workflow}
          key={planning.threadId}
          onConfirmed={() => planningController.onPlanningConfirmed(planning.application.id)}
          onReturnHome={planningController.returnHome}
          onStatusChange={(status) =>
            planningController.updatePlanningStatus(planning.application.id, status)
          }
          onStopHandlerChange={(handler) =>
            planningController.registerStopHandler(planning.application.id, handler)
          }
          onWorkflowChange={(workflow) =>
            planningController.updatePlanningWorkflow(planning.application.id, workflow)
          }
          theme={theme}
          threadId={planning.threadId}
          visible={planningController.visiblePlanningId === planning.application.id}
        />
      ))}

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
            onThemeChange={setTheme}
            theme={theme}
          />
        </div>
      ) : null}
    </>
  )
}

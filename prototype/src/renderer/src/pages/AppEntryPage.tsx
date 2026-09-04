import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { SessionRuntimeProvider } from '../components/AiChatPanel/hooks/useSessionRuntimeStore'
import { useApplicationLifecycleStore } from '../hooks/useApplicationLifecycleStore'
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

// 在欢迎页与应用工作台之间维护顶层导航。
// 新建旅程的规则：需求确认与项目规划全部在工作台需求分析/项目规划阶段内完成，
// 任何应用（含仍在需求分析/项目规划阶段的应用）都直接进入工作台，不再有独立规划弹窗。
function AppEntryContent(): JSX.Element {
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

  // 新建应用：创建完直接进工作台（需求分析阶段），规划在工作台内完成。
  const handleOpenWorkbenchAfterCreate = useCallback(
    (application: ApplicationConfig, lifecycle: ApplicationLifecycle) => {
      void openWorkbench(application, lifecycle)
    },
    [openWorkbench]
  )

  // 从最近项目打开应用：仍处于需求分析/项目规划阶段的应用也直接进工作台；生命周期由工作台冷启动自行校准。
  const handleOpenApplication = useCallback(
    (application: ApplicationConfig) => {
      void openWorkbench(application)
    },
    [openWorkbench]
  )

  // 从工作台直接返回欢迎页，工作台保持挂载，后台任务与工作流继续运行。
  const handleReturnWelcome = (): void => {
    setActiveSurface('welcome')
  }

  // 切换欢迎页/工作台时同步失焦：被隐藏的 surface 会带 aria-hidden，
  // 若焦点仍停留在其内按钮上，浏览器会打印 a11y 警告，演示时造成控制台噪音。
  useLayoutEffect(() => {
    const active = document.activeElement as HTMLElement | null
    if (active && typeof active.blur === 'function') active.blur()
  }, [activeSurface])

  return (
    <>
      <div aria-hidden={activeSurface !== 'welcome'} hidden={activeSurface !== 'welcome'}>
        <WelcomePage
          onOpenApplication={handleOpenApplication}
          onOpenWorkbenchAfterCreate={handleOpenWorkbenchAfterCreate}
        />
      </div>

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

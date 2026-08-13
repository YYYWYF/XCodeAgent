import { Input, Layout, Modal, notification, Progress, Steps } from 'antd'
import RichLoading from '../components/AiChatPanel/components/DesignProgress/RichLoading'
import {
  LoadingOutlined,
  CloudUploadOutlined,
  CheckCircleFilled,
  PlusOutlined,
  HistoryOutlined
} from '@ant-design/icons'
import { useEffect, useRef, useState } from 'react'
import { LeftPanel } from '../components'
import WorkbenchTopBar from '../components/WorkbenchTopBar'
import { WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { latestApplicationLifecycle } from '../hooks/useApplicationLifecycleStore'
import {
  createIterationVersion,
  createRollbackVersion,
  currentVersion,
  findVersion,
  isVersionReleasable
} from '../service/applicationVersions'
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

// 生成版本进度模拟:每步延时 + 模拟 commit sha。
const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => window.setTimeout(resolve, ms))
const mockCommitSha = (): string =>
  Math.random().toString(16).slice(2, 9).padEnd(7, '0').slice(0, 7)

// 生成版本弹框的默认日志(新建应用旅程演示预填):写死一句具体业务功能描述。
function buildDefaultVersionLog(): string {
  return '新增回检单填报与审核功能'
}

// 新迭代版本的初始 lifecycle(回到 collecting_requirement,走完整旅程)。
function makeInitialLifecycle(appId: string, appName: string): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: { id: appId, name: appName },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'collecting_requirement', status: 'running' },
    activeExecutions: {}
  } as ApplicationLifecycle
}

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
  const [viewingVersionId, setViewingVersionId] = useState(application.currentVersionId || '')
  const [rollbackTargetId, setRollbackTargetId] = useState('')
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
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const [applicationPreviewMode, setApplicationPreviewMode] = useState(false)
  const [publishModalOpen, setPublishModalOpen] = useState(false)
  const [iterationModalOpen, setIterationModalOpen] = useState(false)
  // 生成版本:版本描述(提交日志)+ 多步骤进度态(打包/提交码云/打Tag)。
  const [versionDescription, setVersionDescription] = useState('')
  const [generating, setGenerating] = useState<{ stepIndex: number } | null>(null)
  const generatingRef = useRef(false)
  // 切换版本(切历史/回退迭代/发起新迭代)全屏加载:模拟把代码 checkout 到目标版本。
  const [versionSwitching, setVersionSwitching] = useState<{ targetLabel: string } | null>(null)
  const autoPublishShownRef = useRef(false)
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
      className: cx('project-launch-loading')
    })

    startProjectLaunch(workspacePath)
      .then((result) => {
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
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
          void window.xcodeAgent?.projectPreview?.registerWorkspace({
            workspaceRoot: workspacePath
          })
          setPreviewBaseUrl(previewOrigin(result.preview_url))
          setPreviewLaunchError('')
          notification.success({
            message: '项目预览已启动',
            description: '可在预览面板中查看效果',
            placement: 'bottomRight',
            duration: 3
          })
        } else {
          const errorMsg = result.message || '未知错误'
          setPreviewBaseUrl('')
          setPreviewLaunchError(errorMsg)
          notification.warning({
            message: '项目预览启动失败',
            description: `${errorMsg}，可在预览区查看详情`,
            placement: 'bottomRight',
            duration: 3
          })
        }
      })
      .catch((err) => {
        notification.close(loadingKey)
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
        if (!launchStillCurrent) return
        const errorMsg = err instanceof Error ? err.message : '网络请求失败'
        setPreviewBaseUrl('')
        setPreviewLaunchError(errorMsg)
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
        setWorkspaceApplication((prev) => ({
          ...prev,
          ...applicationConfig,
          schema: { ...prev.schema, ...applicationConfig }
        }))
      } catch (error) {
        console.warn('读取工作区 application.json 失败，继续使用已保存应用配置。', error)
      }
      try {
        const inspection = await inspectWorkspacePlanningArtifacts(
          application.workspaceRoot,
          application.id,
          application.currentVersionId
        )
        if (!active) return
        setDevelopmentPlanningPages(inspection.pages)
        setDevelopmentPlanningPageTree(
          Array.isArray(inspection.pageTree) ? inspection.pageTree : []
        )
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

    // 切应用由 AppEntryPage key={application.id} re-mount + useState 初始承载；
    // 这里不再 setWorkspaceApplication(application) —— planningRefreshRevision 变化（设计/开发/审查后刷新）
    // 会重跑本 effect，若用 application prop 覆盖会把发布后的 released 态盖回 iterating。
    void syncWorkspaceFiles()
    window.addEventListener('focus', syncWorkspaceFiles)
    return () => {
      active = false
      window.removeEventListener('focus', syncWorkspaceFiles)
    }
  }, [application.id, application.workspaceRoot, planningRefreshRevision])

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

  // 版本级 lifecycle:阶段推导跟随当前版本(切版本/发起迭代 = 切 lifecycle)。
  // 当前版本快照在新建/迭代时冻结(初始 collecting_requirement),设计旅程的实时推进
  // (ready_for_workbench / launch_project)只更新应用级 lifecycle。版本 snapshot 不随
  // 磁盘 reload 更新,因此这里在渲染时按 revision 取新,保证阶段推导读得到实时状态。
  const activeVersionId = workspaceApplication.currentVersionId || ''
  const viewedApplication = {
    ...workspaceApplication,
    currentVersionId: viewingVersionId || activeVersionId
  }
  const viewedVersion = currentVersion(viewedApplication)
  const isViewingActiveVersion = viewedVersion?.id === activeVersionId
  const versionLifecycle =
    isViewingActiveVersion && viewedVersion?.lifecycle && applicationLifecycle
      ? latestApplicationLifecycle(viewedVersion.lifecycle, applicationLifecycle)
      : viewedVersion?.lifecycle || applicationLifecycle
  const releaseVersion = isViewingActiveVersion ? viewedVersion : undefined

  const versionReleasable = Boolean(
    releaseVersion &&
      versionLifecycle &&
      releaseVersion.status === 'iterating' &&
      isVersionReleasable({ ...releaseVersion, lifecycle: versionLifecycle })
  )

  // 生成版本弹框：审查通过(finalize completed)后自动弹出(仅首次)，也由顶栏生成版本按钮手动打开。
  useEffect(() => {
    if (versionReleasable && !autoPublishShownRef.current) {
      autoPublishShownRef.current = true
      setVersionDescription(buildDefaultVersionLog())
      setPublishModalOpen(true)
    }
  }, [versionReleasable])

  // 生成版本:弹框内多步骤进度(打包资产→提交码云→打Tag),完成后锁定为只读里程碑。
  // 纯前端模拟,不触发工作流节点、不影响对话区。
  const confirmGenerateVersion = (): void => {
    if (!versionReleasable) return
    setVersionDescription(buildDefaultVersionLog())
    setPublishModalOpen(true)
  }
  const handleGenerateVersion = async (): Promise<void> => {
    const description = versionDescription.trim()
    // generatingRef 防双击:闭包里的 generating 在本次点击内不会立即更新。
    if (!releaseVersion || !description || generating || generatingRef.current) return
    generatingRef.current = true
    setGenerating({ stepIndex: 0 })
    const steps = ['打包应用资产', '提交码云', `打 Tag ${releaseVersion.versionLabel}`]
    for (let i = 0; i < steps.length; i += 1) {
      if (i > 0) setGenerating({ stepIndex: i })
      // eslint-disable-next-line no-await-in-loop
      await delay(900)
    }
    const now = Date.now()
    const commitSha = mockCommitSha()
    const publishedLabel = releaseVersion.versionLabel
    setWorkspaceApplication((prev) => {
      const versions = (prev.versions || []).map((v) =>
        v.id === prev.currentVersionId
          ? {
              ...v,
              status: 'released' as const,
              releasedAt: now,
              description,
              gitRef: { commitSha, tag: releaseVersion.versionLabel, committedAt: now },
              artifactSummary: {
                pageIds: prev.pages,
                deployableScript: `deploy-${releaseVersion.versionLabel}.sh`
              },
              snapshot: { pageIds: prev.pages }
            }
          : v
      )
      return { ...prev, versions }
    })
    generatingRef.current = false
    setGenerating(null)
    setPublishModalOpen(false)
    setVersionDescription('')
    notification.success({
      message: '版本已生成',
      description: `${publishedLabel} 已打包提交并打 Tag，锁定为只读版本，可发起新迭代继续开发。`,
      placement: 'bottomRight',
      duration: 4
    })
  }

  /** 切换版本/发起迭代时模拟把代码 checkout 到目标版本,期间全屏加载。 */
  const runVersionSwitch = (targetLabel: string, action: () => void): void => {
    setVersionSwitching({ targetLabel })
    window.setTimeout(() => {
      action()
      setVersionSwitching(null)
    }, 1600)
  }

  /** 只切换工作台查看上下文，不改变当前单向版本头。 */
  const handleVersionSelect = (versionId: string): void => {
    const target = findVersion(workspaceApplication, versionId)
    if (!target || versionId === viewingVersionId) return
    runVersionSwitch(target.versionLabel, () => {
      setViewingVersionId(versionId)
      setApplicationPreviewMode(true)
    })
  }

  /** 基于 historicalVersion 迭代：派生新顺序版本，lifecycle 重置回需求收集（进设计阶段），
   *  版本链以 currentHead 为父、restoredFromVersionId 标记内容来源，历史版本保持只读。 */
  const handleRollbackConfirm = (): void => {
    const restoredVersion = findVersion(workspaceApplication, rollbackTargetId)
    const currentHead = findVersion(workspaceApplication, activeVersionId)
    if (!restoredVersion || !currentHead || restoredVersion.id === currentHead.id) {
      setRollbackTargetId('')
      return
    }
    const resetRevision = (applicationLifecycle?.revision ?? 0) + 1
    const initialLifecycle = {
      ...makeInitialLifecycle(application.id, application.name),
      revision: resetRevision
    }
    const next = {
      ...createRollbackVersion(workspaceApplication.id, currentHead, restoredVersion, Date.now()),
      lifecycle: initialLifecycle
    }
    setRollbackTargetId('')
    runVersionSwitch(next.versionLabel, () => {
      setWorkspaceApplication((current) => ({
        ...current,
        versions: [...(current.versions || []), next],
        currentVersionId: next.id
      }))
      setViewingVersionId(next.id)
      autoPublishShownRef.current = false
      onApplicationLifecycleChange(initialLifecycle)
      // 与发起新迭代一致：重置页面/接口开发任务，从设计阶段（迭代引导词）开始。
      setDevelopmentPlanningPages((pages) =>
        pages.map((page) => ({
          ...page,
          designed: false,
          hasDetailPlan: false,
          detailPlanStatus: 'pending'
        }))
      )
      setDevelopmentPlanningApiContracts((contracts) =>
        contracts.map((contract) => ({
          ...contract,
          endpoints: contract.endpoints.map((endpoint) => ({
            ...endpoint,
            designed: false,
            hasDetailPlan: false
          }))
        }))
      )
      setHasPageDesigns(false)
    })
  }

  // 确认发起新迭代后派生顺序版本，并清空本版本的任务完成态与对话上下文。
  const handleStartIterationConfirm = (): void => {
    const resetRevision = (applicationLifecycle?.revision ?? 0) + 1
    const initialLifecycle = {
      ...makeInitialLifecycle(application.id, application.name),
      revision: resetRevision
    }
    const parent = findVersion(workspaceApplication, activeVersionId)
    if (!parent) return
    const next = createIterationVersion(
      workspaceApplication.id,
      parent,
      initialLifecycle,
      Date.now()
    )
    setIterationModalOpen(false)
    // 发起新迭代是全新空版本,无代码可切,不需要切换加载动画;直接同步建立新版本。
    setWorkspaceApplication((current) => ({
      ...current,
      versions: [...(current.versions || []), next],
      currentVersionId: next.id
    }))
    setViewingVersionId(next.id)
    setDevelopmentPlanningPages((pages) =>
      pages.map((page) => ({
        ...page,
        designed: false,
        hasDetailPlan: false,
        detailPlanStatus: 'pending'
      }))
    )
    setDevelopmentPlanningPageTree((tree) => {
      // 递归重置页面树叶节点，确保新版本从开发任务的未完成态开始。
      const resetNode = (
        node: DevelopmentPlanningPageTreeNode
      ): DevelopmentPlanningPageTreeNode => ({
        ...node,
        ...(node.children?.length
          ? { children: node.children.map(resetNode) }
          : { designed: false, hasDetailPlan: false, detailPlanStatus: 'pending' })
      })
      return tree.map(resetNode)
    })
    setDevelopmentPlanningApiContracts((contracts) =>
      contracts.map((contract) => ({
        ...contract,
        endpoints: contract.endpoints.map((endpoint) => ({
          ...endpoint,
          designed: false,
          hasDetailPlan: false
        }))
      }))
    )
    setHasPageDesigns(false)
    autoPublishShownRef.current = false
    // 应用级 lifecycle 同步重置为新迭代初始态,避免版本 lifecycle 合并把上一版本的完成态盖回来。
    onApplicationLifecycleChange(initialLifecycle)
  }

  // 页面或接口设计运行结束后重新读取规划目录，以持久化结果更新大纲状态。
  const handlePlanningArtifactsRefresh = (): void => {
    setPlanningRefreshRevision((current) => current + 1)
  }

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      {developmentPlanningPagesLoaded ? (
        <WorkbenchPhaseProvider
          key={viewedVersion?.id || workspaceApplication.id}
          applicationId={workspaceApplication.id}
          lifecycle={versionLifecycle}
          locked={!isViewingActiveVersion || viewedVersion?.status === 'released'}
        >
          <div className={cx('workbench-shell-column')}>
            <WorkbenchTopBar
              activeVersionId={activeVersionId}
              application={viewedApplication}
              workspaceRoot={
                workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
              }
              theme={theme}
              onThemeChange={handleThemeChange}
              onReturnWelcome={onReturnWelcome}
              lifecycle={versionLifecycle}
              applicationPreviewMode={applicationPreviewMode}
              onApplicationPreviewModeChange={setApplicationPreviewMode}
              rightPanelOpen={rightPanelOpen}
              onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
              onPublishVersion={confirmGenerateVersion}
              onRollbackVersion={setRollbackTargetId}
              onStartIteration={() => setIterationModalOpen(true)}
              onVersionSelect={handleVersionSelect}
            />
            <div className={cx('workbench-shell-body')}>
              <LeftPanel
                application={viewedApplication}
                applicationLifecycle={versionLifecycle}
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
                onApplicationLifecycleChange={onApplicationLifecycleChange}
                theme={theme}
                rightPanelOpen={rightPanelOpen}
                onRightPanelOpenChange={setRightPanelOpen}
                applicationPreviewMode={applicationPreviewMode}
                onApplicationPreviewModeChange={setApplicationPreviewMode}
                versionViewKey={viewedVersion?.id || ''}
                versionReadOnly={!isViewingActiveVersion || viewedVersion?.status === 'released'}
                versionPreviewOnly={!isViewingActiveVersion}
              />
            </div>
          </div>
        </WorkbenchPhaseProvider>
      ) : null}

      {releaseVersion && publishModalOpen ? (
        <Modal
          centered
          className={cx('workbench-publish-modal', 'is-generate')}
          closable={!generating}
          footer={null}
          maskClosable={!generating}
          onCancel={() => {
            if (!generating) setPublishModalOpen(false)
          }}
          open
          width={460}
        >
          <div className={cx('workbench-publish-modal-inner')}>
            <header className={cx('workbench-publish-modal-header')}>
              <span className={cx('workbench-publish-modal-icon')} aria-hidden="true">
                <CloudUploadOutlined />
              </span>
              <span className={cx('workbench-publish-modal-title')}>
                <strong>生成版本 {releaseVersion.versionLabel}</strong>
                <small>打包应用资产 · 提交码云 · 打 Tag</small>
              </span>
            </header>
            <div className={cx('workbench-publish-modal-body')}>
              {generating ? (
                <div className={cx('workbench-generate-progress')}>
                  <Progress
                    percent={Math.round(((generating.stepIndex + 1) / 3) * 100)}
                    showInfo={false}
                    strokeColor={{ from: '#6b3cf0', to: '#3f6cf5' }}
                  />
                  <Steps current={generating.stepIndex} direction="vertical" size="small">
                    <Steps.Step title="打包应用资产" description="页面 / 接口 / 数据源 / 配置" />
                    <Steps.Step title="提交码云" description="创建提交记录" />
                    <Steps.Step
                      title={`打 Tag ${releaseVersion.versionLabel}`}
                      description="标记版本里程碑"
                    />
                  </Steps>
                </div>
              ) : (
                <>
                  <div className={cx('workbench-generate-reminder')}>
                    <p className={cx('workbench-generate-reminder-title')}>
                      生成版本将执行以下操作:
                    </p>
                    <ul>
                      <li>打包本版本全部应用资产(页面 / 接口 / 数据源 / 配置)</li>
                      <li>
                        提交到码云仓库并打上版本 Tag <strong>{releaseVersion.versionLabel}</strong>
                      </li>
                      <li>
                        生成后该版本<strong>锁定为只读</strong>,后续改动需「发起新迭代」
                      </li>
                    </ul>
                  </div>
                  <div className={cx('workbench-generate-field')}>
                    <label className={cx('workbench-generate-field-label')}>
                      <span className={cx('workbench-generate-required')}>*</span>
                      版本日志
                    </label>
                    <Input.TextArea
                      value={versionDescription}
                      onChange={(e) => setVersionDescription(e.target.value)}
                      rows={3}
                      maxLength={200}
                      showCount
                      placeholder="请填写当前版本的提交日志"
                    />
                  </div>
                </>
              )}
            </div>
            <footer className={cx('workbench-publish-modal-footer')}>
              <button
                className={cx('workbench-publish-modal-cancel')}
                type="button"
                disabled={!!generating}
                onClick={() => setPublishModalOpen(false)}
              >
                取消
              </button>
              <button
                className={cx('workbench-publish-modal-confirm')}
                type="button"
                disabled={!!generating || !versionDescription.trim()}
                onClick={handleGenerateVersion}
              >
                {generating ? (
                  <>
                    <LoadingOutlined aria-hidden="true" /> 生成中…
                  </>
                ) : (
                  <>
                    <CloudUploadOutlined aria-hidden="true" /> 确认生成
                  </>
                )}
              </button>
            </footer>
          </div>
        </Modal>
      ) : null}

      {viewedVersion && iterationModalOpen ? (
        <Modal
          centered
          className={cx('workbench-publish-modal')}
          closable
          footer={null}
          onCancel={() => setIterationModalOpen(false)}
          open
          width={420}
        >
          <div className={cx('workbench-publish-modal-inner')}>
            <header className={cx('workbench-publish-modal-header')}>
              <span
                className={cx('workbench-publish-modal-icon', 'is-iteration')}
                aria-hidden="true"
              >
                <PlusOutlined />
              </span>
              <span className={cx('workbench-publish-modal-title')}>
                <strong>发起新迭代</strong>
                <small>创建新版本并重新进入设计阶段</small>
              </span>
            </header>
            <div className={cx('workbench-publish-modal-body')}>
              <p className={cx('workbench-publish-modal-lead')}>
                将基于{' '}
                <strong className={cx('workbench-publish-modal-version')}>
                  {viewedVersion.versionLabel}
                </strong>
                创建 v{viewedVersion.major}.{viewedVersion.minor + 1}
                。新版本会从设计阶段开始，使用全新的对话记录。
              </p>
              <div className={cx('workbench-publish-modal-meta')}>
                <CheckCircleFilled aria-hidden="true" /> 已生成版本保持锁定，可随时切换查看
              </div>
            </div>
            <footer className={cx('workbench-publish-modal-footer')}>
              <button
                className={cx('workbench-publish-modal-cancel')}
                type="button"
                onClick={() => setIterationModalOpen(false)}
              >
                取消
              </button>
              <button
                className={cx('workbench-publish-modal-confirm')}
                type="button"
                onClick={handleStartIterationConfirm}
              >
                <PlusOutlined aria-hidden="true" /> 确认发起
              </button>
            </footer>
          </div>
        </Modal>
      ) : null}

      {rollbackTargetId ? (
        <Modal
          centered
          className={cx('workbench-publish-modal')}
          closable
          footer={null}
          onCancel={() => setRollbackTargetId('')}
          open
          width={420}
        >
          <div className={cx('workbench-publish-modal-inner')}>
            <header className={cx('workbench-publish-modal-header')}>
              <span
                className={cx('workbench-publish-modal-icon', 'is-rollback')}
                aria-hidden="true"
              >
                <HistoryOutlined />
              </span>
              <span className={cx('workbench-publish-modal-title')}>
                <strong>基于此版本迭代</strong>
                <small>以历史版本为基础生成新迭代版本</small>
              </span>
            </header>
            <div className={cx('workbench-publish-modal-body')}>
              <p className={cx('workbench-publish-modal-lead')}>
                将基于{' '}
                <strong className={cx('workbench-publish-modal-version')}>
                  {findVersion(workspaceApplication, rollbackTargetId)?.versionLabel || '所选版本'}
                </strong>{' '}
                的内容生成新迭代版本{' '}
                <strong className={cx('workbench-publish-modal-version')}>
                  {(() => {
                    const head = findVersion(workspaceApplication, activeVersionId)
                    return head ? `v${head.major}.${head.minor + 1}` : '新版本'
                  })()}
                </strong>
                ，以该历史版本为基础继续开发。原有版本保持只读、可随时切换查看，不会被覆盖。
              </p>
              <div className={cx('workbench-publish-modal-meta')}>
                <CheckCircleFilled aria-hidden="true" /> 历史版本保持只读，可随时切换查看
              </div>
            </div>
            <footer className={cx('workbench-publish-modal-footer')}>
              <button
                className={cx('workbench-publish-modal-cancel')}
                type="button"
                onClick={() => setRollbackTargetId('')}
              >
                取消
              </button>
              <button
                className={cx('workbench-publish-modal-confirm')}
                type="button"
                onClick={handleRollbackConfirm}
              >
                <HistoryOutlined aria-hidden="true" /> 确认迭代
              </button>
            </footer>
          </div>
        </Modal>
      ) : null}

      {versionSwitching ? (
        <div className={cx('workbench-version-switching-mask')} role="status" aria-live="polite">
          <div className={cx('workbench-version-switching-card')}>
            <RichLoading bare title={`正在加载 ${versionSwitching.targetLabel} 版本应用资产…`} />
          </div>
        </div>
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

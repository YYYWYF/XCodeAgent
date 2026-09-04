import { Layout, notification } from 'antd'
import {
  HourglassOutlined,
  MoonOutlined
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { LeftPanel } from '../components'
import WorkbenchTopBar from '../components/WorkbenchTopBar'
import WorkbenchVersionModals from '../components/WorkbenchVersionModals'
import BackgroundTaskDrawer, {
  type BackgroundTaskItem
} from '../components/BackgroundTaskDrawer'
import {
  BACKGROUND_TASK_KIND_LABEL,
  BACKGROUND_TASK_NEXT_STEP_LABEL,
  BACKGROUND_TASK_SYSTEM_LABEL,
  retainBackgroundTasksWithinApplications,
  switchBackgroundTaskQueue,
  type BackgroundTask,
  type BackgroundTaskSystem
} from '../backgroundTasks'
import { useBackgroundTasks, backgroundTasksForVersion } from '../hooks/useBackgroundTasks'
import type { TestCaseGenerationTaskType } from '../testCasePreparation'
import AuxiliaryDrawer, {
  type AuxiliaryDrawerMode,
  type ConversationManagementContent
} from '../components/AiChatPanel/components/AuxiliaryDrawer'
import { WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  loadCachedApplications,
  loadWorkspaceApplicationConfig
} from '../service/applicationStorage'
import { getApplicationLifecycle } from '../service/applicationLifecycle'
import { latestApplicationLifecycle } from '../hooks/useApplicationLifecycleStore'
// 前端本地合成 lifecycle 快照的 revision 必须与剧本共享计数器，避免与下一帧撞号被拒合并
import { nextSyntheticLifecycleRevision } from '../mock/scripts/revision'
import {
  createIterationVersion,
  createRollbackVersion,
  currentVersion,
  findVersion,
  isVersionReleasable
} from '../service/applicationVersions'
import {
  resetDevelopmentPlanningApiContracts,
  resetDevelopmentPlanningPageTree,
  resetDevelopmentPlanningPages
} from '../service/developmentPlanningState'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../typings'
import type { WorkbenchArtifactProgress } from '../workbenchDomain'
import { cx } from '../utils'
import { useProjectPreviewLaunch } from '../hooks/useProjectPreviewLaunch'
import { useAsyncTestCasePreparation } from '../hooks/useAsyncTestCasePreparation'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onReturnWelcome: () => void
}

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

/** 抽屉空间有限：运行中只对外表达整体是否在执行（执行中/排队中），不展示内部子节点阶段。 */
function backgroundTaskPhaseText(task: BackgroundTask): string {
  if (task.status === 'queued') return '排队中'
  return '执行中'
}

/** 把一条后台任务映射为抽屉条目；两套系统共用同一映射，保证交互结构完全一致。 */
function mapBackgroundTaskItem(
  task: BackgroundTask,
  onAccept: (taskId: string) => void,
  onSwitchQueue: (taskId: string, target: BackgroundTaskSystem) => void,
  acceptDisabled: boolean
): BackgroundTaskItem {
  const nextStepPending = Boolean(
    task.kind === 'artifact_implementation' && task.nextStep && !task.nextStep.done
  )
  // 排队中（未开始）的任务允许在两条算力队列间切换；运行中/已结束不提供入口。
  const switchTarget: BackgroundTaskSystem | undefined =
    task.status === 'queued' ? (task.pool === 'async' ? 'tide' : 'async') : undefined
  return {
    key: task.id,
    status: task.status,
    title: task.title,
    kindLabel: BACKGROUND_TASK_KIND_LABEL[task.kind],
    statusText:
      task.status === 'queued' || task.status === 'running'
        ? backgroundTaskPhaseText(task)
        : task.kind === 'test_case_generation' && task.status === 'completed'
          ? '已就绪'
          : undefined,
    nextStepLabel: nextStepPending
      ? BACKGROUND_TASK_NEXT_STEP_LABEL[task.nextStep!.type]
      : undefined,
    onNextStep: nextStepPending ? () => onAccept(task.id) : undefined,
    nextStepDisabled: nextStepPending && acceptDisabled,
    switchLabel:
      switchTarget !== undefined
        ? `转到${BACKGROUND_TASK_SYSTEM_LABEL[switchTarget]}`
        : undefined,
    onSwitchQueue:
      switchTarget !== undefined ? () => onSwitchQueue(task.id, switchTarget) : undefined
  }
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
  onReturnWelcome
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  const [viewingVersionId, setViewingVersionId] = useState(application.currentVersionId || '')
  const [rollbackTargetId, setRollbackTargetId] = useState('')
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false)
  const [, setHasPageDesigns] = useState(false)
  const [developmentPlanningPages, setDevelopmentPlanningPages] = useState<
    DevelopmentPlanningPageOption[]
  >([])
  const [developmentPlanningPageTree, setDevelopmentPlanningPageTree] = useState<
    DevelopmentPlanningPageTreeNode[]
  >([])
  const [developmentPlanningApiContracts, setDevelopmentPlanningApiContracts] = useState<
    DevelopmentPlanningApiContract[]
  >([])
  const [developmentPlanningEntities, setDevelopmentPlanningEntities] = useState<
    DevelopmentPlanningEntity[]
  >([])
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)

  // 进入工作台时自动异步启动项目预览（首次创建和重新进入均生效）。
  const { previewBaseUrl, previewLaunchError } = useProjectPreviewLaunch({
    applicationId: application.id,
    workspaceRoot: application.workspaceRoot || '',
    projectParentPath: application.projectParentPath || ''
  })
  const [iterationModalOpen, setIterationModalOpen] = useState(false)
  // 审查阶段进入口：聊天面板上报“允许进入”（全部开发产物完成），顶部阶段条发起“进入确认”。
  // “允不允许进入”与“当前进没进去”是两个状态，分别由这两个 state 承载。
  const [testingEntryAvailable, setTestingEntryAvailable] = useState(false)
  const [testingEntryRequest, setTestingEntryRequest] = useState(0)
  // 开发准入门（计划确认后的进入开发弹框）：与测试/审查共用“可进入 + 重新唤起请求”模式。
  const [developmentEntryAvailable, setDevelopmentEntryAvailable] = useState(false)
  const [developmentEntryRequest, setDevelopmentEntryRequest] = useState(0)
  // 项目规划准入门（需求文档确认后的进入项目规划弹框）：与开发准入门同一模式。
  const [planningEntryAvailable, setPlanningEntryAvailable] = useState(false)
  const [planningEntryRequest, setPlanningEntryRequest] = useState(0)
  const [reviewEntryAvailable, setReviewEntryAvailable] = useState(false)
  const [reviewEntryRequest, setReviewEntryRequest] = useState(0)
  const [developmentArtifactProgress, setDevelopmentArtifactProgress] =
    useState<WorkbenchArtifactProgress>({ completed: 0, total: 0 })
  const [testPreparationOpenRequest, setTestPreparationOpenRequest] = useState(0)
  // 两套任务系统各自的抽屉与临时任务抽屉统一由工作台页持有：同一时间只允许打开一个。
  const [backgroundTasksDrawer, setBackgroundTasksDrawer] = useState<BackgroundTaskSystem | null>(
    null
  )
  // 点击待验收任务的「验收」入口时通过自增请求通知聊天面板启动验收工作流。
  const [backgroundTaskAcceptRequest, setBackgroundTaskAcceptRequest] = useState<{
    nonce: number
    taskId: string
  }>({ nonce: 0, taskId: '' })
  // 验收工作流进行中的任务：入口触发后即禁用其它入口，结束（成败皆算）后解除。
  const [acceptanceInFlight, setAcceptanceInFlight] = useState<Record<string, true>>({})
  const handleAcceptBackgroundTask = useCallback((taskId: string): void => {
    setAcceptanceInFlight((current) => ({ ...current, [taskId]: true }))
    setBackgroundTasksDrawer(null)
    setBackgroundTaskAcceptRequest((current) => ({ nonce: current.nonce + 1, taskId }))
  }, [])
  const handleBackgroundTaskAcceptanceSettled = useCallback((taskId: string): void => {
    setAcceptanceInFlight((current) => {
      const next = { ...current }
      delete next[taskId]
      return next
    })
  }, [])
  const [testCaseGenerationTaskType, setTestCaseGenerationTaskType] =
    useState<TestCaseGenerationTaskType>()
  const [auxiliaryDrawerMode, setAuxiliaryDrawerMode] = useState<AuxiliaryDrawerMode | null>(null)
  // 任务管理抽屉的内容快照：打开抽屉时经聊天面板查询获得，抽屉打开期间定时重查保持新鲜。
  const [conversationManagementContent, setConversationManagementContent] =
    useState<ConversationManagementContent>()
  // 新建/删除/切换查看对象都是异步落地，且落地时长不定：
  // 抽屉打开期间定时重查快照，徽标、选中态与列表在动作完成后自动跟上，无需各动作各自回调。
  /** 打开任务管理抽屉，并互斥关闭两套任务系统的抽屉。 */
  const openConversationManagement = (): void => {
    setBackgroundTasksDrawer(null)
    const base = getConversationManagementContentRef.current?.()
    if (base) {
      setConversationManagementContent(base)
    }
    setAuxiliaryDrawerMode('conversation-management')
  }
  // 抽屉打开期间轮询快照；切走或关闭时停止。
  useEffect(() => {
    if (auxiliaryDrawerMode !== 'conversation-management') return
    const refresh = (): void => {
      const next = getConversationManagementContentRef.current?.()
      if (next) setConversationManagementContent(next)
    }
    refresh()
    const timer = window.setInterval(refresh, 700)
    return () => window.clearInterval(timer)
  }, [auxiliaryDrawerMode])
  /** 打开指定任务系统的队列抽屉，并互斥关闭任务管理抽屉。 */
  const openBackgroundTasksDrawer = (system: BackgroundTaskSystem): void => {
    setAuxiliaryDrawerMode(null)
    setBackgroundTasksDrawer(system)
  }
  /** 再点已打开的同系统入口时收起抽屉；入口是「展开/收起」的切换而不是单向打开。 */
  const toggleBackgroundTasksDrawer = (system: BackgroundTaskSystem): void => {
    if (backgroundTasksDrawer === system) {
      closeBackgroundTasksDrawer()
      return
    }
    openBackgroundTasksDrawer(system)
  }
  /** 关闭后台任务抽屉。 */
  const closeBackgroundTasksDrawer = (): void => {
    setBackgroundTasksDrawer(null)
  }
  /** 关闭辅助抽屉。 */
  const closeAuxiliaryDrawer = (): void => {
    setAuxiliaryDrawerMode(null)
  }
  // 任务管理内容查询函数由聊天面板注册（函数在其内部创建），用 ref 转接避免抽屉打开链路依赖渲染时序。
  const getConversationManagementContentRef = useRef<(() => ConversationManagementContent) | undefined>()
  const handleConversationManagementReady = useCallback((query: () => ConversationManagementContent) => {
    getConversationManagementContentRef.current = query
  }, [])
  // 用例生成队列动态绑定：顶部芯片打开用例任务实际所在的任务系统抽屉。
  const testCaseQueueSystem: BackgroundTaskSystem =
    testCaseGenerationTaskType === 'tide' ? 'tide' : 'async'
  // 生成版本:版本描述(提交日志)+ 多步骤进度态(打包/提交码云/打Tag)。
  const [publishModalOpen, setPublishModalOpen] = useState(false)
  const [versionDescription, setVersionDescription] = useState('')
  const [generating, setGenerating] = useState<{ stepIndex: number } | null>(null)
  const generatingRef = useRef(false)
  // 切换版本(切历史/回退迭代/发起新迭代)全屏加载:模拟把代码 checkout 到目标版本。
  const [versionSwitching, setVersionSwitching] = useState<{ targetLabel: string } | null>(null)
  const autoPublishShownRef = useRef(false)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const entryStartedAtRef = useRef(Date.now())

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
        setDevelopmentPlanningEntities(
          Array.isArray(inspection.entities) ? inspection.entities : []
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
        setDevelopmentPlanningEntities([])
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
  const testCasePreparationEnabled = new Set([
    'generating_ui_designs',
    'awaiting_ui_design_confirmation',
    'generating_technical_plan',
    'awaiting_technical_plan_confirmation',
    'generating_application_template_files',
    'ready_for_workbench'
  ]).has(String(versionLifecycle?.initialization?.stage || ''))
  const testCasePreparation = useAsyncTestCasePreparation(
    workspaceApplication.id,
    viewedVersion?.id || activeVersionId || 'current',
    testCasePreparationEnabled,
    // 类型未选定前保持为空：后台任务在开发准入门选定类型时才创建。
    testCaseGenerationTaskType
  )
  const releaseVersion = isViewingActiveVersion ? viewedVersion : undefined

  const versionReleasable = Boolean(
    releaseVersion &&
      versionLifecycle &&
      releaseVersion.status === 'iterating' &&
      isVersionReleasable({ ...releaseVersion, lifecycle: versionLifecycle })
  )

  // 将当前版本的统一后台任务流水映射为抽屉条目：任务类型随条目携带，
  // 运行中显示执行阶段文案，待验收任务附「验收」入口。
  const allBackgroundTasks = useBackgroundTasks()
  useEffect(() => {
    // 任务流水跟着应用走：启动工作台时清理不属于任何已知应用的任务，
    // 避免不持久化应用的旧任务跨旅程残留在抽屉里。
    const validIds = new Set(['app-pms-new'])
    loadCachedApplications().forEach((app) => validIds.add(app.id))
    retainBackgroundTasksWithinApplications([...validIds])
  }, [])
  const versionBackgroundTasks = useMemo(
    () =>
      backgroundTasksForVersion(
        allBackgroundTasks,
        workspaceApplication.id,
        viewedVersion?.id || activeVersionId || 'current'
      ),
    [activeVersionId, allBackgroundTasks, viewedVersion?.id, workspaceApplication.id]
  )
  /** 点击排队中任务的「转到××任务」入口：把任务迁到另一条算力队列重新排队。 */
  const handleSwitchBackgroundTaskQueue = useCallback(
    (taskId: string, target: BackgroundTaskSystem): void => {
      switchBackgroundTaskQueue(taskId, target)
    },
    []
  )
  /** 两套任务系统的抽屉条目按各自流水独立映射；交互结构一致，仅数据源不同。 */
  const backgroundTaskItems = useMemo<Record<BackgroundTaskSystem, BackgroundTaskItem[]>>(
    () => ({
      // 抽屉是后台队列任务的唯一入口；同步执行在工作流内当场完成，不进任何任务池。
      async: versionBackgroundTasks
        .filter((task) => task.pool === 'async')
        .map((task) =>
          mapBackgroundTaskItem(
            task,
            handleAcceptBackgroundTask,
            handleSwitchBackgroundTaskQueue,
            Boolean(acceptanceInFlight[task.id])
          )
        ),
      tide: versionBackgroundTasks
        .filter((task) => task.pool === 'tide')
        .map((task) =>
          mapBackgroundTaskItem(
            task,
            handleAcceptBackgroundTask,
            handleSwitchBackgroundTaskQueue,
            Boolean(acceptanceInFlight[task.id])
          )
        )
    }),
    [
      acceptanceInFlight,
      handleAcceptBackgroundTask,
      handleSwitchBackgroundTaskQueue,
      versionBackgroundTasks
    ]
  )
  // 两套系统抽屉的说明文案：压缩到头部一行内完整显示，算力来源与计费口径与选择卡一致。
  const backgroundTaskDescriptions: Record<BackgroundTaskSystem, string> = {
    async: '常规算力队列，后台执行，消耗码豆。',
    tide: '闲时算力队列，低优先级执行，不消耗码豆。'
  }
  // 抽屉头部徽标与左侧菜单入口共用同一套图标语言：同步=闪电，异步=沙漏，潮汐=月亮。
  const backgroundTaskIcons: Record<BackgroundTaskSystem, ReactElement> = {
    async: <HourglassOutlined />,
    tide: <MoonOutlined />
  }

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
    // 发布时把当前 lifecycle 固化进版本快照：版本是"当前资产快照"，回看历史版本
    // 必须停在发布时刻的旅程位置，而不是退回新建时冻结的初始 collecting_requirement。
    const releasedLifecycle = versionLifecycle
    setWorkspaceApplication((prev) => {
      const versions = (prev.versions || []).map((v) =>
        v.id === prev.currentVersionId
          ? {
              ...v,
              status: 'released' as const,
              releasedAt: now,
              description,
              ...(releasedLifecycle ? { lifecycle: releasedLifecycle } : {}),
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
    })
  }

  /** 基于 historicalVersion 迭代：派生新顺序版本，lifecycle 重置回需求收集（进需求分析阶段），
   *  版本链以 currentHead 为父、restoredFromVersionId 标记内容来源，历史版本保持只读。 */
  const handleRollbackConfirm = (): void => {
    const restoredVersion = findVersion(workspaceApplication, rollbackTargetId)
    const currentHead = findVersion(workspaceApplication, activeVersionId)
    if (!restoredVersion || !currentHead || restoredVersion.id === currentHead.id) {
      setRollbackTargetId('')
      return
    }
    const resetRevision = nextSyntheticLifecycleRevision(applicationLifecycle?.revision ?? 0)
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
      // 与发起新迭代一致：重置页面/接口开发任务，从需求分析阶段（迭代引导词）开始。
      setDevelopmentPlanningPages(resetDevelopmentPlanningPages)
      setDevelopmentPlanningPageTree(resetDevelopmentPlanningPageTree)
      setDevelopmentPlanningApiContracts(resetDevelopmentPlanningApiContracts)
      setHasPageDesigns(false)
    })
  }

  // 确认发起新迭代后派生顺序版本，并清空本版本的任务完成态与对话上下文。
  const handleStartIterationConfirm = (): void => {
    const resetRevision = nextSyntheticLifecycleRevision(applicationLifecycle?.revision ?? 0)
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
    setDevelopmentPlanningPages(resetDevelopmentPlanningPages)
    setDevelopmentPlanningPageTree(resetDevelopmentPlanningPageTree)
    setDevelopmentPlanningApiContracts(resetDevelopmentPlanningApiContracts)
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
    <Layout className={cx('workbench-shell')} data-theme="light">
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
              onReturnWelcome={onReturnWelcome}
              lifecycle={versionLifecycle}
              onPublishVersion={confirmGenerateVersion}
              onRollbackVersion={setRollbackTargetId}
              onStartIteration={() => setIterationModalOpen(true)}
              onVersionSelect={handleVersionSelect}
              canEnterTestingStage={testingEntryAvailable}
              onRequestEnterTesting={() => setTestingEntryRequest((count) => count + 1)}
              canEnterDevelopmentStage={developmentEntryAvailable}
              onRequestEnterDevelopment={() => setDevelopmentEntryRequest((count) => count + 1)}
              canEnterPlanningStage={planningEntryAvailable}
              onRequestEnterPlanning={() => setPlanningEntryRequest((count) => count + 1)}
              canEnterReviewStage={reviewEntryAvailable}
              onRequestEnterReview={() => setReviewEntryRequest((count) => count + 1)}
              developmentArtifactProgress={developmentArtifactProgress}
              planConfirmed={testCasePreparationEnabled}
              testCasePreparation={testCasePreparation.snapshot}
              onOpenTestPreparation={() =>
                setTestPreparationOpenRequest((count) => count + 1)
              }
              onOpenTestCaseQueue={() => toggleBackgroundTasksDrawer(testCaseQueueSystem)}
              testCaseQueueOpen={backgroundTasksDrawer === testCaseQueueSystem}
            />
            <div className={cx('workbench-shell-body')}>
              <LeftPanel
                application={viewedApplication}
                applicationLifecycle={versionLifecycle}
                developmentPlanningReady={developmentPlanningPagesLoaded}
                developmentPlanningPages={developmentPlanningPages}
                developmentPlanningPageTree={developmentPlanningPageTree}
                developmentPlanningApiContracts={developmentPlanningApiContracts}
                developmentPlanningEntities={developmentPlanningEntities}
                editorMode={editorMode}
                onApplicationUpdate={handleApplicationUpdate}
                onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                previewBaseUrl={previewBaseUrl}
                previewLaunchError={previewLaunchError}
                onApplicationLifecycleChange={onApplicationLifecycleChange}
                versionViewKey={viewedVersion?.id || ''}
                versionReadOnly={!isViewingActiveVersion || viewedVersion?.status === 'released'}
                versionPreviewOnly={!isViewingActiveVersion}
                testingEntryRequest={testingEntryRequest}
                onTestingEntryAvailableChange={setTestingEntryAvailable}
                developmentEntryRequest={developmentEntryRequest}
                onDevelopmentEntryAvailableChange={setDevelopmentEntryAvailable}
                planningEntryRequest={planningEntryRequest}
                onPlanningEntryAvailableChange={setPlanningEntryAvailable}
                reviewEntryRequest={reviewEntryRequest}
                onReviewEntryAvailableChange={setReviewEntryAvailable}
                onDevelopmentArtifactProgressChange={setDevelopmentArtifactProgress}
                testCasePreparation={testCasePreparation.snapshot}
                testPreparationOpenRequest={testPreparationOpenRequest}
                onRetryTestCases={testCasePreparation.retry}
                onTestCaseGenerationTaskTypeChange={setTestCaseGenerationTaskType}
                backgroundTasksDrawer={backgroundTasksDrawer}
                onOpenBackgroundTasks={toggleBackgroundTasksDrawer}
                onOpenConversationManagement={openConversationManagement}
                conversationDrawerOpen={auxiliaryDrawerMode === 'conversation-management'}
                onConversationManagementReady={handleConversationManagementReady}
                onCloseAuxiliaryDrawer={() => setAuxiliaryDrawerMode(null)}
                backgroundTaskAcceptRequest={backgroundTaskAcceptRequest}
                onRequestBackgroundTaskContinuation={handleAcceptBackgroundTask}
                onBackgroundTaskAcceptanceSettled={handleBackgroundTaskAcceptanceSettled}
              />
              {/* 后台任务与临时任务抽屉：统一挂在裁剪宿主内，从左侧菜单栏右边线滑出，
                  宿主裁剪保证抽屉任何时刻都不会越过菜单栏或盖到菜单栏之上。 */}
              <div className={cx('workbench-drawer-host')}>
                {/* 抽屉不是模态图层：打开时铺一层透明遮罩拦截工作区首次点击用于快速收起，
                    左侧菜单栏在宿主之外不受影响，菜单入口仍可直接切换抽屉。 */}
                {(backgroundTasksDrawer || auxiliaryDrawerMode) && (
                  <div
                    aria-hidden="true"
                    className={cx('workbench-drawer-outside-mask')}
                    onClick={() => {
                      closeBackgroundTasksDrawer()
                      closeAuxiliaryDrawer()
                    }}
                  />
                )}
                {(['async', 'tide'] as const).map((system) => (
                  <BackgroundTaskDrawer
                    description={backgroundTaskDescriptions[system]}
                    emptyText={`当前没有${BACKGROUND_TASK_SYSTEM_LABEL[system]}。`}
                    icon={backgroundTaskIcons[system]}
                    key={system}
                    onClose={closeBackgroundTasksDrawer}
                    open={backgroundTasksDrawer === system}
                    tasks={backgroundTaskItems[system]}
                    title={BACKGROUND_TASK_SYSTEM_LABEL[system]}
                  />
                ))}
                {auxiliaryDrawerMode ? (
                  <AuxiliaryDrawer
                    conversationManagement={conversationManagementContent}
                    mode={auxiliaryDrawerMode}
                    onClose={() => setAuxiliaryDrawerMode(null)}
                    onOpenConversationManagement={() =>
                      setAuxiliaryDrawerMode('conversation-management')
                    }
                    onOpenTemporaryConversation={() => setAuxiliaryDrawerMode('temporary-conversation')}
                    onRetryTestCases={testCasePreparation.retry}
                    testPreparation={testCasePreparation.snapshot}
                  />
                ) : null}
              </div>
            </div>

          </div>
        </WorkbenchPhaseProvider>
      ) : null}

      <WorkbenchVersionModals
        activeVersionId={activeVersionId}
        application={workspaceApplication}
        generating={generating}
        iterationBaseVersionId={viewedVersion && iterationModalOpen ? viewedVersion.id : undefined}
        publishDescription={versionDescription}
        publishVersionLabel={
          releaseVersion && publishModalOpen ? releaseVersion.versionLabel : undefined
        }
        rollbackTargetVersionId={rollbackTargetId || undefined}
        switchingTargetLabel={versionSwitching?.targetLabel}
        onCancelPublish={() => setPublishModalOpen(false)}
        onCancelIteration={() => setIterationModalOpen(false)}
        onCancelRollback={() => setRollbackTargetId('')}
        onConfirmIteration={handleStartIterationConfirm}
        onConfirmRollback={handleRollbackConfirm}
        onDescriptionChange={setVersionDescription}
        onGenerate={handleGenerateVersion}
      />

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

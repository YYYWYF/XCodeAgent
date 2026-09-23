import { Layout, message, notification } from 'antd'
import { randomUUID } from '@ag-ui/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import WorkbenchVersionModals from '../components/WorkbenchVersionModals'
import type { IterationBranchChoice } from '../components/WorkbenchVersionModals'
import { UncommittedChangesProvider, WorkbenchPhaseProvider } from '../context'
import {
  inspectWorkspacePlanningArtifacts,
  isApplicationCreationComplete,
  loadWorkspaceApplicationConfig,
  saveWorkspaceApplicationConfig
} from '../service/applicationStorage'
import {
  createApplicationLifecycle,
  getApplicationLifecycle
} from '../service/applicationLifecycle'
import {
  branchIterationScope,
  createBranchRecord,
  createInitialBranch,
  currentBranch,
  findBranch,
  isBranchPublishable,
  isViewingHistoricalBranch,
  mergeBranches,
  resolveBranchChain
} from '../service/applicationBranches'
import { publishVersion } from '../service/versionPublish'
import { startIteration } from '../service/iterationService'
import { createRepositoryBranch } from '../service/repositoryBranch'
import type {
  RequirementSpecDraftSaveResult,
  WorkflowRevisionContinuationHandoff
} from '../service/applicationPagePlanning'
import type { ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import type {
  ApplicationBranch,
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import { developmentArtifactTotals, developmentCompletedCount } from '../developmentArtifacts'
import { cx } from '../utils'
import './WorkbenchPage.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onApplicationLifecycleReset: (lifecycle: ApplicationLifecycle) => void
  onEntryLoadFailure: () => void
  onReturnWelcome: () => void
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onIterationStarted: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  onStartIterationPlanning: (applicationId: string, request: string) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: Theme) => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  onSavePlanningRequirementSpec: (
    spec: Record<string, unknown>
  ) => Promise<RequirementSpecDraftSaveResult>
  onStopPlanning: () => Promise<void>
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 从工作台错误卡片重试设计阶段规划任务。 */
  onRetryPlanning?: () => void
  /** 通过专用动作重试失败的模板能力更新。 */
  onRetryTemplateReconcile?: () => void
  /** 当前应用唯一的 Planning 业务状态。 */
  planningState?: ApplicationPlanningCurrentState
  theme: Theme
}

type Theme = 'light' | 'dark'
type WorkbenchEntryStage = 'loading' | 'leaving' | 'ready'

const WORKBENCH_ENTRY_MIN_VISIBLE_MS = 520
const WORKBENCH_ENTRY_FADE_MS = 280
const WORKBENCH_ENTRY_TIMEOUT_MS = 15_000

// 将未知加载异常转换为可展示的工作台入口错误。
function formatWorkbenchEntryError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message.trim() : fallback
}

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({
  application,
  applicationLifecycle,
  onApplicationLifecycleChange,
  onApplicationLifecycleReset,
  onEntryLoadFailure,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStartDesignStageRevision,
  onIterationStarted,
  onStartIterationPlanning,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  onSavePlanningRequirementSpec,
  onStopPlanning,
  generatingTemplate,
  onRetryPlanning,
  onRetryTemplateReconcile,
  planningState,
  theme
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  const [developmentPlanningPagesLoaded, setDevelopmentPlanningPagesLoaded] = useState(false)
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
    DevelopmentPlanningEntityOption[]
  >([])
  const [chatSessionHistoryReady, setChatSessionHistoryReady] = useState(false)
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  const [entryStage, setEntryStage] = useState<WorkbenchEntryStage>('loading')
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  // —— 应用分支（提交并推送/发起新迭代/切换分支）状态 ——
  // 当前查看的分支名；为空时取当前分支（branchName）。
  const [viewingBranchName, setViewingBranchName] = useState<string>('')
  // 提交推送弹框：推送中三步进度（打包/提交/推送分支），null 表示未在提交。
  const [versionGenerating, setVersionGenerating] = useState<{ stepIndex: number } | null>(null)
  // 提交推送弹框：变更说明草稿。
  const [publishDescription, setPublishDescription] = useState('')
  // 提交推送弹框是否开启。
  const [publishModalOpen, setPublishModalOpen] = useState(false)
  // 发起新迭代弹框是否开启。
  const [iterationModalOpen, setIterationModalOpen] = useState(false)
  // 发起新迭代：用户对分支的选择（当前分支继续 / 新建分支）。
  const [iterationChoice, setIterationChoice] = useState<IterationBranchChoice>({
    mode: 'current'
  })
  // 分支切换全屏加载的目标名；为空时不显示。
  const [switchingTargetLabel, setSwitchingTargetLabel] = useState<string | undefined>(undefined)
  const entryStartedAtRef = useRef(Date.now())
  const entryStageRef = useRef<WorkbenchEntryStage>('loading')
  const entryFailureHandledRef = useRef(false)
  // 模板是否就绪（lifecycle=ready_for_workbench），用于校准正式规划产物。
  const lifecycleReadyForWorkbench = isApplicationCreationComplete(applicationLifecycle)

  // 入口失败只处理一次：提示原因、返回首页，并由顶层卸载损坏的工作台实例。
  const failWorkbenchEntry = useCallback(
    (reason: string): void => {
      if (entryStageRef.current !== 'loading' || entryFailureHandledRef.current) return
      entryFailureHandledRef.current = true
      message.error(`工作台加载失败，已返回首页：${reason}`)
      onEntryLoadFailure()
    },
    [onEntryLoadFailure]
  )

  useEffect(() => {
    entryStageRef.current = entryStage
  }, [entryStage])

  useEffect(() => {
    if (entryStage !== 'loading') return
    const timer = window.setTimeout(
      () => failWorkbenchEntry('同步项目配置、规划产物或历史会话超时。'),
      WORKBENCH_ENTRY_TIMEOUT_MS
    )
    return () => window.clearTimeout(timer)
  }, [entryStage, failWorkbenchEntry])

  useEffect(() => {
    let active = true

    // 同步可选的应用配置和规划产物；窗口重新聚焦时只校准可能被外部修改的文件。
    const syncWorkspaceFiles = async (): Promise<void> => {
      if (!application.workspaceRoot) {
        failWorkbenchEntry('应用缺少有效的工作区路径。')
        return
      }
      try {
        const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot)
        if (!active) return
        setWorkspaceApplication((prev) => ({
          ...application,
          ...applicationConfig,
          // 磁盘是分支表的权威来源；只在磁盘没有分支表时才回退到内存那份。
          // 详见 resolveBranchChain 的说明（此前无条件取内存，会把磁盘上的多分支冲成单条）。
          ...resolveBranchChain({
            diskBranches: applicationConfig.branches,
            diskBranchName: applicationConfig.branchName,
            memoryBranches: prev.branches,
            memoryBranchName: prev.branchName
          })
        }))
      } catch (error) {
        console.warn('读取工作区 application.json 失败，终止本次工作台加载。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '读取工作区 application.json 失败。'))
        return
      }
      try {
        const inspection = await inspectWorkspacePlanningArtifacts(application.workspaceRoot)
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
        if (!inspection.ready) {
          console.warn('工作区规划产物不完整。', inspection)
          if (lifecycleReadyForWorkbench) {
            const details = [...inspection.missing, ...inspection.invalid].join('、')
            failWorkbenchEntry(
              details ? `工作区规划产物缺失或损坏：${details}` : '工作区规划产物不完整。'
            )
          }
        }
      } catch (error) {
        if (!active) return
        setDevelopmentPlanningPages([])
        setDevelopmentPlanningPageTree([])
        setDevelopmentPlanningApiContracts([])
        setDevelopmentPlanningEntities([])
        console.warn('检查 specs/plans 规划产物失败。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '检查工作区规划产物失败。'))
      } finally {
        if (active) setDevelopmentPlanningPagesLoaded(true)
      }
    }

    // 首次进入由初始状态承载加载门禁；后续刷新保留当前内容，避免工作台反复清空闪烁。
    // 仅当应用 id 变化时才用 prop 覆盖，避免发起新迭代后 lifecycle 变化触发 effect 回退分支。
    setWorkspaceApplication((prev) => (prev.id === application.id ? prev : application))
    void syncWorkspaceFiles()
    window.addEventListener('focus', syncWorkspaceFiles)
    return () => {
      active = false
      window.removeEventListener('focus', syncWorkspaceFiles)
    }
  }, [application, failWorkbenchEntry, lifecycleReadyForWorkbench, planningRefreshRevision])

  useEffect(() => {
    let active = true
    const workspaceRoot = application.workspaceRoot
    if (!workspaceRoot) return

    // 每次进入一个工作区只做一次冷启动校准；后续状态由 Workflow AG-UI 事件实时合并。
    getApplicationLifecycle({ workspaceRoot, id: application.id, appName: application.appName })
      .then((lifecycle) => {
        if (active) onApplicationLifecycleChange(lifecycle)
      })
      .catch((error) => {
        if (!active) return
        console.warn('读取工作台应用生命周期失败，终止本次工作台加载。', error)
        failWorkbenchEntry(formatWorkbenchEntryError(error, '读取应用生命周期失败。'))
      })
    return () => {
      active = false
    }
  }, [application.id, application.workspaceRoot, failWorkbenchEntry, onApplicationLifecycleChange])

  // 应用首次进入工作台时初始化首条分支记录，分支名取自新建表单。
  // 已有 branches 的应用（如发起新迭代后重新进入）跳过，保留分支表。
  useEffect(() => {
    if (!applicationLifecycle) return
    setWorkspaceApplication((prev) => {
      if (prev.branches && prev.branches.length > 0) return prev
      const branchName = prev.branchName || 'dev'
      const initialBranch = createInitialBranch(branchName, applicationLifecycle, Date.now())
      return { ...prev, branches: [initialBranch], branchName: initialBranch.name }
    })
  }, [applicationLifecycle])

  // 会话恢复失败时终止入口等待；普通的未就绪状态仍交给恢复流程或超时兜底。
  const handleSessionHistoryReadyChange = useCallback(
    (ready: boolean, error?: string): void => {
      setChatSessionHistoryReady(ready)
      if (error) failWorkbenchEntry(error)
    },
    [failWorkbenchEntry]
  )

  useEffect(() => {
    if (!developmentPlanningPagesLoaded || !chatSessionHistoryReady || entryStage !== 'loading') {
      return
    }
    const remainingVisibleTime = Math.max(
      0,
      WORKBENCH_ENTRY_MIN_VISIBLE_MS - (Date.now() - entryStartedAtRef.current)
    )
    const timer = window.setTimeout(() => setEntryStage('leaving'), remainingVisibleTime)
    return () => window.clearTimeout(timer)
  }, [chatSessionHistoryReady, developmentPlanningPagesLoaded, entryStage])

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

  // —— 应用分支操作回调 ——
  // 当前查看分支（viewingBranchName 为空时取当前分支）。
  const activeBranchName = workspaceApplication.branchName || ''
  const viewedBranch =
    findBranch(workspaceApplication, viewingBranchName) || currentBranch(workspaceApplication)
  const isViewingActiveVersion = viewedBranch?.name === activeBranchName
  // 查看非当前分支时锁定阶段切换，只能回看。
  const versionLocked = !isViewingActiveVersion
  // 是否在回看历史分支（非当前分支）。顶部栏与内容区共用这一个口径。
  const viewingHistoricalVersion = isViewingHistoricalBranch(
    activeBranchName,
    viewedBranch?.name
  )
  // 当前分支顶部计数必须跟随当前正式规划目录；新迭代清空目录后应立即显示 0/0，
  // 不能把 lifecycle 内为增量门禁保留的上一轮完成事实展示成当前分支产物。
  const topBarLifecycle = isViewingActiveVersion ? applicationLifecycle : viewedBranch?.lifecycle
  // 阶段 Provider 的重挂载键：分支名 + 迭代令牌。同一条分支上继续迭代时分支名不变，
  // 必须靠迭代令牌让 Provider 重挂载，否则它会沿用上一轮的手动阶段覆盖、界面停在
  // 上一轮阶段（详见 branchIterationScope 的说明）。
  const phaseProviderKey = branchIterationScope(
    viewedBranch?.name || workspaceApplication.id,
    topBarLifecycle
  )
  const activeDevelopmentRecords = [
    ...developmentPlanningPages.map(
      (page) => applicationLifecycle?.developmentArtifacts?.pages[page.pageId]
    ),
    ...developmentPlanningApiContracts.flatMap((contract) =>
      contract.endpoints.map(
        (endpoint) =>
          applicationLifecycle?.developmentArtifacts?.endpoints[
          endpoint.apiContractId || contract.id
          ]?.[endpoint.id]
      )
    ),
    ...developmentPlanningEntities.map(
      (entity) => applicationLifecycle?.developmentArtifacts?.entities[entity.id]
    )
  ]
  const topBarDevelopmentTotals = isViewingActiveVersion
    ? {
      completed: developmentCompletedCount(activeDevelopmentRecords),
      total: activeDevelopmentRecords.length
    }
    : topBarLifecycle?.developmentArtifacts
      ? developmentArtifactTotals(topBarLifecycle.developmentArtifacts)
      : undefined
  // 当前分支是否可提交推送（验收通过等条件齐全）。
  const versionReleasable =
    isViewingActiveVersion &&
    isBranchPublishable(applicationLifecycle || viewedBranch?.lifecycle)
  /**
   * 把内存里的应用配置写回 application.json，**先与磁盘合并分支表**。
   *
   * 直接写内存那份会静默抹掉磁盘上的分支：内存状态可能陈旧于磁盘（发起迭代/提交推送
   * 都是先改内存再写盘，期间若有别的写入或状态未及时同步）。线上出现过新分支已写盘、
   * 随后一次写回把 application.json 退回成只有旧分支，界面顶部只剩一条。
   * 见 mergeBranches 的说明。
   */
  const persistApplicationConfig = useCallback(async (next: ApplicationConfig): Promise<void> => {
    let merged = next
    try {
      const disk = await loadWorkspaceApplicationConfig(next.workspaceRoot)
      const chain = mergeBranches({
        memoryBranches: next.branches,
        diskBranches: disk.branches,
        memoryBranchName: next.branchName,
        diskBranchName: disk.branchName
      })
      merged = { ...next, ...chain }
    } catch (error) {
      // 读不到磁盘配置时按原样写回：不能因为合并失败就阻断用户操作。
      console.warn('读取磁盘 application.json 失败，按内存状态写回。', error)
    }
    await saveWorkspaceApplicationConfig(merged.workspaceRoot, merged)
    // 内存也采纳合并结果，避免界面与磁盘再次脱节。
    setWorkspaceApplication((prev) =>
      prev.id === merged.id
        ? { ...merged, branches: merged.branches, branchName: merged.branchName }
        : prev
    )
  }, [])

  const autoPublishShownRef = useRef(false)

  // 打开提交并推送弹框。
  const handleOpenPublish = useCallback((): void => {
    setPublishDescription('')
    setVersionGenerating(null)
    setPublishModalOpen(true)
  }, [])

  // 进入工作台时若当前迭代已可发布，视为自动弹框已消费（避免一进来就打断阶段回看）；
  // 仅本会话内旅程推进到可发布时才自动弹出发布弹框。
  useEffect(() => {
    if (versionReleasable) {
      autoPublishShownRef.current = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 审查通过后自动弹出发布弹框（仅首次）。
  useEffect(() => {
    if (versionReleasable && !autoPublishShownRef.current) {
      autoPublishShownRef.current = true
      setPublishDescription('')
      setPublishModalOpen(true)
    }
  }, [versionReleasable])

  // 确认提交并推送：调后端真实 git 提交并推送到当前分支，三步进度实时推进。
  // 分支不锁定；提交时冻结当前 lifecycle、资产快照与提交事实，并持久化到 application.json。
  const handleGenerateVersion = useCallback(async (): Promise<void> => {
    if (!viewedBranch) return
    const branchName = viewedBranch.name
    const description = publishDescription.trim()
    setVersionGenerating({ stepIndex: 0 })
    try {
      const result = await publishVersion(
        {
          workspaceRoot: workspaceApplication.workspaceRoot,
          repoUrl: workspaceApplication.repoUrl,
          branchName,
          description
        },
        {
          onUpdate: (value) => {
            if (value.status === 'in_progress' && value.progress) {
              const stage = value.progress.stage
              const stepIndex = stage === 'package' ? 0 : stage === 'commit' ? 1 : 2
              setVersionGenerating({ stepIndex })
            }
          }
        }
      )
      const now = Date.now()
      const frozenLifecycle = applicationLifecycle
      const target = findBranch(workspaceApplication, branchName)
      if (!target) {
        setVersionGenerating(null)
        return
      }
      // 冻结提交时刻的 lifecycle、资产快照与提交事实，回看该分支时停在那一刻。
      // 分支不锁定：它仍是当前分支，可以继续改、继续提交。
      const frozen: ApplicationBranch = {
        ...target,
        description,
        ...(frozenLifecycle ? { lifecycle: frozenLifecycle } : {}),
        artifactSummary: {
          pageIds: workspaceApplication.pages,
          deployableScript: `deploy-${branchName}.sh`
        },
        snapshot: { pageIds: workspaceApplication.pages },
        gitRef: { commitSha: result.commitSha || '', committedAt: now }
      }
      const nextApplication: ApplicationConfig = {
        ...workspaceApplication,
        branches: (workspaceApplication.branches || []).map((branch) =>
          branch.name === frozen.name ? frozen : branch
        )
      }
      setWorkspaceApplication(nextApplication)
      // 持久化到 application.json，刷新后分支状态不丢失。
      try {
        await persistApplicationConfig(nextApplication)
      } catch (saveError) {
        console.warn('保存 application.json 失败，分支状态仅保留在内存。', saveError)
      }
      setVersionGenerating(null)
      setPublishModalOpen(false)
      const repoUrl = String(workspaceApplication.repoUrl || '').trim()
      notification.success({
        message: '已提交并推送',
        description: repoUrl
          ? `本次改动已提交至 ${repoUrl} 的分支 ${branchName}，可以继续在该分支上开发。`
          : `本次改动已提交到分支 ${branchName}，可以继续在该分支上开发。`,
        placement: 'bottomRight',
        duration: 4
      })
    } catch (error) {
      setVersionGenerating(null)
      message.error(error instanceof Error ? error.message : '提交并推送失败')
    }
  }, [
    applicationLifecycle,
    persistApplicationConfig,
    publishDescription,
    viewedBranch,
    workspaceApplication
  ])

  // 确认发起新迭代：回到需求分析阶段。用户可选择在当前分支继续，或新建一条分支。
  // 调后端清空 .devagentstudio 规划产物（保留 AGENTS.md），重置 lifecycle 为 collecting_requirement。
  const handleConfirmIteration = useCallback(async (): Promise<void> => {
    if (!viewedBranch || !applicationLifecycle) return
    const baseBranchName = viewedBranch.name
    const targetBranchName =
      iterationChoice.mode === 'new' ? iterationChoice.branchName.trim() : baseBranchName
    setIterationModalOpen(false)
    setSwitchingTargetLabel(targetBranchName)
    try {
      // 1. 新建分支时必须**先**建分支再清空产物 —— 新分支要指向当前这次已提交的代码，
      //    晚于清空就会把新分支建在残缺的树上。后端会切到新分支并推送到远端。
      let branchCreationFailed = false
      if (iterationChoice.mode === 'new') {
        const created = await createRepositoryBranch({
          workspaceRoot: workspaceApplication.workspaceRoot,
          branchName: targetBranchName
        })
        if (created.status !== 'pushed') {
          branchCreationFailed = true
          message.warning(
            `分支 ${targetBranchName} 未推送到远端：${created.message || '原因未知'}。已切换到该分支，稍后可重试推送。`
          )
        }
      }
      // 2. 调后端清空 .devagentstudio 规划产物（保留 AGENTS.md + application.json）。
      await startIteration({
        workspaceRoot: workspaceApplication.workspaceRoot,
        branchName: targetBranchName,
        description: viewedBranch.description || ''
      })
      // 2.5 清空环境数据目录中该工作区的全部会话历史，
      // 避免上一轮的对话卡片串入新迭代。
      await window.devAgentStudio?.sessions?.clearWorkspace({
        workspaceRoot: workspaceApplication.workspaceRoot
      })
      // 3. 创建全新的 lifecycle（collecting_requirement），revision 从 1 重新开始。
      //    继承上一轮**已完成**的产物进度：新迭代保留已有工程代码，那些产物仍然存在，
      //    不该重新变回"未开发"——否则"全部产物完成"的测试门禁在增量迭代里永远满足不了
      //    （本轮构建范围只覆盖改动的产物，不会再去开发其余已完成的产物）。
      const inheritedArtifacts =
        viewedBranch.lifecycle?.developmentArtifacts ?? applicationLifecycle.developmentArtifacts
      const newLifecycle = await createApplicationLifecycle(
        workspaceApplication,
        randomUUID(),
        inheritedArtifacts
      )
      // 标记为 awaiting_user，确保 planning runtime 不自动发起分析，等用户输入需求后再触发。
      const awaitingLifecycle: ApplicationLifecycle = {
        ...newLifecycle,
        initialization: { ...newLifecycle.initialization, status: 'awaiting_user' }
      }
      onApplicationLifecycleReset(awaitingLifecycle)
      // 3.5 登记 planning state（restoreArtifactsFromDisk=false 避免 reconcile 触发自动分析），
      // 让用户输入需求后能触发 planning workflow。
      const newThreadId = awaitingLifecycle.initialization?.threadId
      if (newThreadId) {
        onIterationStarted(workspaceApplication, newThreadId, awaitingLifecycle)
      }
      // 4. 更新分支表：在当前分支继续时刷新该分支的 lifecycle 并**清掉本轮提交事实**；
      //    新建分支时追加一条记录并把当前分支指针移过去。
      //    清 gitRef 是必须的：它同时是"本轮已提交"的标记（顶部按钮据此在
      //    「提交并推送」与「发起新迭代」之间切换），不清的话新一轮一进来就直接
      //    显示「发起新迭代」，用户没法提交这一轮的改动。
      const existingBranches = workspaceApplication.branches || []
      const nextBranches =
        iterationChoice.mode === 'new'
          ? [...existingBranches, createBranchRecord(targetBranchName, awaitingLifecycle, Date.now())]
          : existingBranches.map((branch) =>
            branch.name === targetBranchName
              ? { ...branch, lifecycle: awaitingLifecycle, gitRef: undefined }
              : branch
          )
      const nextApp: ApplicationConfig = {
        ...workspaceApplication,
        branches: nextBranches,
        branchName: targetBranchName
      }
      setWorkspaceApplication(nextApp)
      // 5. 持久化 application.json（分支表更新，写回前与磁盘合并）。
      await persistApplicationConfig(nextApp)
      // 6. 阶段覆盖与浏览进度按「分支 + 迭代令牌」作用域持久化，本轮是新的作用域，
      //    旧的覆盖读不到。**但光靠作用域不够**：阶段 Provider 必须真的重挂载才会
      //    重新读取，所以它的 key 带上了迭代令牌（见 phaseProviderKey）。
      //    少了这一步，界面会沿用上一轮的手动阶段覆盖，发起新迭代后仍停在上一轮阶段。
      // 7. 重置前端规划状态，新迭代从空白开始。
      setDevelopmentPlanningPages([])
      setDevelopmentPlanningPageTree([])
      setDevelopmentPlanningApiContracts([])
      setDevelopmentPlanningEntities([])
      setViewingBranchName('')
      autoPublishShownRef.current = false
      setSwitchingTargetLabel(undefined)
      setIterationChoice({ mode: 'current' })
      message.success(
        branchCreationFailed
          ? `已发起新迭代（分支 ${targetBranchName}），但分支未推送到远端。`
          : `已发起新迭代（分支 ${targetBranchName}），开启新的旅程。`
      )
    } catch (error) {
      setSwitchingTargetLabel(undefined)
      message.error(error instanceof Error ? error.message : '发起新迭代失败')
    }
  }, [
    applicationLifecycle,
    iterationChoice,
    persistApplicationConfig,
    viewedBranch,
    workspaceApplication,
    onApplicationLifecycleReset,
    onIterationStarted
  ])

  // 切换查看分支：展示切换加载层后切到目标分支（只读回看）。
  const handleBranchSelect = useCallback(
    (branchName: string): void => {
      const target = findBranch(workspaceApplication, branchName)
      if (!target) return
      setSwitchingTargetLabel(target.name)
      window.setTimeout(() => {
        setViewingBranchName(branchName)
        setSwitchingTargetLabel(undefined)
      }, 600)
    },
    [workspaceApplication]
  )

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      {developmentPlanningPagesLoaded ? (
        <WorkbenchPhaseProvider
          key={phaseProviderKey}
          applicationId={workspaceApplication.id}
          versionId={viewedBranch?.name || workspaceApplication.id}
          lifecycle={topBarLifecycle}
          locked={versionLocked}
        >
          <UncommittedChangesProvider
            workspaceRoot={
              workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
            }
            // 构建推进时 lifecycle revision 会变（执行状态流转、任务完成），
            // 用它驱动未提交快照重读 —— 否则构建期间窗口不失焦，角标会停在构建前的 0。
            refreshKey={String(applicationLifecycle?.revision ?? '')}
          >
            <div className={cx('workbench-shell-column')}>
              <WorkbenchTopBar
                application={workspaceApplication}
                workspaceRoot={
                  workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
                }
                onReturnWelcome={onReturnWelcome}
                lifecycle={topBarLifecycle}
                developmentTotals={topBarDevelopmentTotals}
                rightPanelOpen={rightPanelOpen}
                onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
                onPublishBranch={handleOpenPublish}
                onStartIteration={() => {
                  // 每次打开都从「在当前分支继续」开始，避免沿用上一次的选择。
                  setIterationChoice({ mode: 'current' })
                  setIterationModalOpen(true)
                }}
                onBranchSelect={handleBranchSelect}
                viewingBranchName={viewingBranchName}
                versionReadOnly={viewingHistoricalVersion}
              />
              <div className={cx('workbench-shell-body')}>
                <LeftPanel
                  application={workspaceApplication}
                  applicationLifecycle={applicationLifecycle}
                  developmentPlanningReady={developmentPlanningPagesLoaded}
                  developmentPlanningPages={developmentPlanningPages}
                  developmentPlanningPageTree={developmentPlanningPageTree}
                  developmentPlanningApiContracts={developmentPlanningApiContracts}
                  developmentPlanningEntities={developmentPlanningEntities}
                  editorMode={editorMode}
                  onApplicationUpdate={handleApplicationUpdate}
                  onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                  previewBaseUrl=""
                  previewLaunchError=""
                  previewLaunchLoading={false}
                  onApplicationLifecycleChange={onApplicationLifecycleChange}
                  onReturnWelcome={onReturnWelcome}
                  onSubmitPlanningClarification={onSubmitPlanningClarification}
                  onStartDesignStageRevision={onStartDesignStageRevision}
                  onStartIterationPlanning={(request) =>
                    onStartIterationPlanning(workspaceApplication.id, request)
                  }
                  onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
                  onThemeChange={handleThemeChange}
                  onPlanningStreamReady={onPlanningStreamReady}
                  onSavePlanningRequirementSpec={onSavePlanningRequirementSpec}
                  onStopPlanning={onStopPlanning}
                  onSessionHistoryReadyChange={handleSessionHistoryReadyChange}
                  generatingTemplate={generatingTemplate}
                  onRetryPlanning={onRetryPlanning}
                  onRetryTemplateReconcile={onRetryTemplateReconcile}
                  planningState={planningState}
                  theme={theme}
                  rightPanelOpen={rightPanelOpen}
                  onRightPanelOpenChange={setRightPanelOpen}
                  // 只读双 tab 只针对回看历史分支（非当前分支）。
                  versionReadOnly={viewingHistoricalVersion}
                  // 该分支名：历史分支的应用文件与预览都按它读取当时的内容。
                  viewedBranchName={viewedBranch?.name}
                />
              </div>
            </div>
            <WorkbenchVersionModals
              application={workspaceApplication}
              publishBranchName={
                publishModalOpen && viewedBranch ? viewedBranch.name : undefined
              }
              publishRepoUrl={
                publishModalOpen
                  ? String(workspaceApplication.repoUrl || '').trim() || undefined
                  : undefined
              }
              publishDescription={publishDescription}
              onDescriptionChange={setPublishDescription}
              generating={versionGenerating}
              onCancelPublish={() => {
                if (versionGenerating) return
                setPublishModalOpen(false)
              }}
              onGenerate={handleGenerateVersion}
              iterationModalOpen={iterationModalOpen}
              iterationChoice={iterationChoice}
              onIterationChoiceChange={setIterationChoice}
              onCancelIteration={() => setIterationModalOpen(false)}
              onConfirmIteration={handleConfirmIteration}
              switchingTargetLabel={switchingTargetLabel}
            />
          </UncommittedChangesProvider>
        </WorkbenchPhaseProvider>
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
            <div className={cx('workbench-entry-kicker')}>DevAgent Studio WORKSPACE</div>
            <h1>正在进入工作台</h1>
            <p>正在同步项目配置、页面设计与历史会话</p>
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

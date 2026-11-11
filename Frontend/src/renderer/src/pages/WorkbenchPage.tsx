import { useApplicationConfigRefresh } from './useApplicationConfigRefresh'
import { sameApplicationWorkspace } from '../service/applicationWorkspace'
import { useWorkbenchEntry } from './useWorkbenchEntry'
import { useWorkbenchPlanningCatalog } from './useWorkbenchPlanningCatalog'
import { Layout, message, notification } from 'antd'
import { randomUUID } from '@ag-ui/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import WorkbenchVersionModals from '../components/WorkbenchVersionModals'
import type {
  HistoricalIterationDirtyChoice,
  IterationBranchChoice
} from '../components/WorkbenchVersionModals'
import { UncommittedChangesProvider, WorkbenchPhaseProvider } from '../context'
import {
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
import {
  discardVersionControl,
  inspectAllVersionControl,
  summarizeVersionControl
} from '../service/versionControl'
import { startIteration } from '../service/iterationService'
import { asMessageClause, createRepositoryBranch } from '../service/repositoryBranch'
import type {
  RequirementSpecDraftSaveResult,
  WorkflowRevisionContinuationHandoff
} from '../service/applicationPagePlanning'
import type { ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import type {
  ApplicationBranch,
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationPlanningDebugStage,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowDebugOptions,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import { developmentArtifactTotals, developmentPlanningTotals } from '../developmentArtifacts'
import { cx } from '../utils'
import './WorkbenchPage.less'

const EMPTY_CATALOG = {
  pages: [] as DevelopmentPlanningPageOption[],
  pageTree: [] as DevelopmentPlanningPageTreeNode[],
  apiContracts: [] as DevelopmentPlanningApiContract[],
  entities: [] as DevelopmentPlanningEntityOption[]
}

// 提交推送弹框「提交前缀」的默认值：与后端 git_commit_message.py 的 BOARD_COMMIT_NUMBER
// + #comment + 常用类型保持一致。后端在收到已带编号的提交信息时幂等返回，不会重复补前缀，
// 所以这里把编号显性化给用户编辑是安全的。
const DEFAULT_PUBLISH_PREFIX = 'Z000000-0000 #comment chore:'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onApplicationLifecycleReset: (lifecycle: ApplicationLifecycle) => void
  onEntryLoadFailure: () => void
  onReturnWelcome: () => void
  returningWelcome?: boolean
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string,
    technicalPlanningConversation?: boolean,
    stageEntry?: import('../service/planningStageHandoff').PlanningStageEntry,
    conversationTarget?: import('../typings').PlanningConversationTarget
  ) => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart, target?: import('../service/planningStageHandoff').PlanningStageTarget) => Promise<void>
  onIterationStarted: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  onStartIterationPlanning: (
    applicationId: string,
    request: string,
    workflowDebug?: WorkflowDebugOptions,
    debugStage?: ApplicationPlanningDebugStage
  ) => Promise<void>
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
  onRetryPlanning?: (restartInitialPlanning?: boolean) => void | Promise<void>
  /** 底部连接恢复后只读同步规划投影，不派发节点重试。 */
  onReconcilePlanning?: () => void
  /** 保留独立的模板初始化重试动作。 */
  onRetryTemplateGeneration?: () => void
  /** 通过专用动作重试失败的模板能力更新。 */
  onRetryTemplateReconcile?: () => void
  /** 当前应用唯一的 Planning 业务状态。 */
  planningState?: ApplicationPlanningCurrentState
  theme: Theme
}

type Theme = 'light' | 'dark'

/** 由当前版本号建议下一个迭代版本号：尾号 +1（v1_0 → v1_1），无尾号则追加 _1。 */
function suggestNextBranchName(currentBranchName: string, existingBranchNames: string[]): string {
  const base = (currentBranchName || 'v1').trim() || 'v1'
  const match = base.match(/^(.*?)(\d+)$/)
  const taken = new Set(existingBranchNames.map((name) => name.trim()))
  // 从当前分支的尾号开始递增；若当前分支无尾号则在其后追加 _1 起跳。
  let prefix = base
  let counter = 0
  if (match) {
    prefix = match[1]
    counter = Number.parseInt(match[2], 10)
  } else {
    prefix = `${base}_`
  }
  for (let attempt = 0; attempt < 1000; attempt += 1) {
    counter += 1
    const candidate = `${prefix}${counter}`
    if (!taken.has(candidate)) return candidate
  }
  return `${prefix}${counter}_${Date.now()}`
}

// 组织工作台状态，并以正式 ProjectPlan 页面清单驱动首个页面规划选择。
function WorkbenchPage({
  application,
  applicationLifecycle,
  onApplicationLifecycleChange,
  onApplicationLifecycleReset,
  onEntryLoadFailure,
  onReturnWelcome,
  returningWelcome = false,
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
  onReconcilePlanning,
  onRetryTemplateGeneration,
  onRetryTemplateReconcile,
  planningState,
  theme
}: Props): JSX.Element {
  const editorMode: EditorMode = 'frontend'
  const [workspaceApplication, setWorkspaceApplication] = useState(application)
  useApplicationConfigRefresh(application.workspaceRoot, setWorkspaceApplication)

  const {
    entryStage,
    developmentPlanningPagesLoaded,
    failWorkbenchEntry,
    markConfigurationLoaded,
    markPlanningArtifactsLoaded,
    handleSessionHistoryReadyChange
  } = useWorkbenchEntry(onEntryLoadFailure)
  const [planningRefreshRevision, setPlanningRefreshRevision] = useState(0)
  // —— 应用分支（提交并推送/发起新迭代/切换分支）状态 ——
  // 当前查看的分支名；为空时取当前分支（branchName）。
  const [viewingBranchName, setViewingBranchName] = useState<string>('')
  // 提交推送弹框：推送中三步进度（打包/提交/推送分支），null 表示未在提交。
  const [versionGenerating, setVersionGenerating] = useState<{ stepIndex: number } | null>(null)
  // 提交推送弹框：变更说明草稿。
  const [publishDescription, setPublishDescription] = useState('')
  // 提交推送弹框：提交前缀（看板编号 + #comment + 提交类型），跨弹窗保留，
  // 用户改过一次后下次打开不再重置。默认值与后端 git_commit_message.py 的兜底格式一致，
  // 后端 ensure_board_number 检测到已带编号就不会重复添加，所以前端拼好后端不会再加工。
  const [publishPrefix, setPublishPrefix] = useState(DEFAULT_PUBLISH_PREFIX)
  // 提交推送弹框：是否正在根据本次改动生成变更说明（生成期间弹窗照常可用）。
  const [descriptionGenerating, setDescriptionGenerating] = useState(false)
  // 提交推送弹框是否开启。
  const [publishModalOpen, setPublishModalOpen] = useState(false)
  // 发起新迭代弹框是否开启。
  const [iterationModalOpen, setIterationModalOpen] = useState(false)
  // 发起新迭代：固定新建分支（便于回退），这里仅存用户输入的新版本号。
  const [iterationChoice, setIterationChoice] = useState<IterationBranchChoice>({
    mode: 'new',
    branchName: ''
  })
  // 「基于此版本迭代」弹框：起点历史分支名；为空串表示弹框关闭。
  const [historicalIterationSource, setHistoricalIterationSource] = useState<string>('')
  // 「基于此版本迭代」弹框：新分支名草稿。
  const [historicalIterationBranchName, setHistoricalIterationBranchName] = useState<string>('')
  // 「基于此版本迭代」弹框：未提交改动数；打开弹框时一次性拉取。
  const [historicalIterationUncommittedCount, setHistoricalIterationUncommittedCount] =
    useState<number>(0)
  // 「基于此版本迭代」弹框：未提交改动处理方式（commit = 审阅并提交 / discard = 丢掉）。
  const [historicalIterationDirtyChoice, setHistoricalIterationDirtyChoice] =
    useState<HistoricalIterationDirtyChoice>('discard')
  // 分支切换全屏加载的目标名；为空时不显示。
  const [switchingTargetLabel, setSwitchingTargetLabel] = useState<string | undefined>(undefined)
  // 模板是否就绪（lifecycle=ready_for_workbench），用于校准正式规划产物。
  const lifecycleReadyForWorkbench = isApplicationCreationComplete(applicationLifecycle)

  const catalog = useWorkbenchPlanningCatalog(application.workspaceRoot, JSON.stringify([
    planningRefreshRevision, lifecycleReadyForWorkbench,
    planningState?.workflow?.runId, planningState?.transportState
  ]))
  const developmentPlanningPages = catalog.inspection?.pages || EMPTY_CATALOG.pages
  const developmentPlanningPageTree = catalog.inspection?.pageTree || EMPTY_CATALOG.pageTree
  const developmentPlanningApiContracts = catalog.inspection?.apiContracts || EMPTY_CATALOG.apiContracts
  const developmentPlanningEntities = catalog.inspection?.entities || EMPTY_CATALOG.entities

  useEffect(() => {
    let active = true
    // 配置与目录分别读取，配置读取的等待或失败不阻止目录发起真实 IPC。
    const syncConfiguration = async (): Promise<void> => {
      if (!application.workspaceRoot) { failWorkbenchEntry('workspace_missing'); return }
      try {
        const applicationConfig = await loadWorkspaceApplicationConfig(application.workspaceRoot)
        if (!active) return
        markConfigurationLoaded()
        setWorkspaceApplication((prev) => {
          const pages = Array.isArray(prev.pages) && prev.pages.length > 0 ? prev.pages : ['页面']
          return {
            ...prev, ...applicationConfig, pages, defaultPage: prev.defaultPage || pages[0],
            ...resolveBranchChain({
              diskBranches: applicationConfig.branches, diskBranchName: applicationConfig.branchName,
              memoryBranches: prev.branches, memoryBranchName: prev.branchName
            })
          }
        })
      } catch (error) {
        if (!active) return
        console.warn('读取工作区 application.json 失败，终止本次工作台加载。', error)
        failWorkbenchEntry('configuration')
      }
    }
    setWorkspaceApplication((prev) => (sameApplicationWorkspace(prev, application) ? prev : application))
    void syncConfiguration()
    return () => { active = false }
  }, [application, failWorkbenchEntry, markConfigurationLoaded])

  useEffect(() => {
    // 完成读取或明确展示读取失败后放行入口，失败不再通过 finally 清空目录并伪装成功。
    if (catalog.settled) markPlanningArtifactsLoaded()
  }, [catalog.settled, markPlanningArtifactsLoaded])

  useEffect(() => {
    const inspection = catalog.inspection
    if (!inspection || inspection.ready || inspection.planningUnconfirmed ||
      inspection.catalogIssues.length || !lifecycleReadyForWorkbench || !application.workspaceRoot) return
    let active = true
    // 目录自身错误由目录重试面板承载；其它正式文件的完整性仍核对当前生命周期后提示。
    const warnIncompleteArtifacts = async (): Promise<void> => {
      let lifecycle: ApplicationLifecycle | undefined
      try {
        lifecycle = await getApplicationLifecycle({
          workspaceRoot: application.workspaceRoot!, id: application.id, appName: application.appName
        })
      } catch (error) { console.warn('无法同步应用生命周期，继续打开工作台。', error) }
      if (!active) return
      if (lifecycle) {
        onApplicationLifecycleChange(lifecycle)
        if (!isApplicationCreationComplete(lifecycle)) return
      }
      message.warning('规划文件缺失或不完整。执行时会提供对应步骤的重新生成入口。')
    }
    void warnIncompleteArtifacts()
    return () => { active = false }
  }, [catalog.inspection, lifecycleReadyForWorkbench, application, onApplicationLifecycleChange])

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
        // 生命周期只用于恢复规划状态，不作为打开已有工作区的准入条件。
        console.warn('读取工作台应用生命周期失败，继续保留工作台。', error)
      })
    return () => {
      active = false
    }
  }, [application.id, application.appName, application.workspaceRoot, onApplicationLifecycleChange])

  // 应用首次进入工作台时初始化首条分支记录，分支名取自新建表单。
  // 已有 branches 的应用（如发起新迭代后重新进入）跳过，保留分支表。
  useEffect(() => {
    if (!applicationLifecycle) return
    setWorkspaceApplication((prev) => {
      if (prev.branches && prev.branches.length > 0) return prev
      // 兜底分支名与新建表单默认值保持一致（公司内 Gitee 只允许字母、数字、下划线，
      // 所以是 v1_0 而不是 v1.0）。
      const branchName = prev.branchName || 'v1_0'
      const initialBranch = createInitialBranch(branchName, applicationLifecycle, Date.now())
      return { ...prev, branches: [initialBranch], branchName: initialBranch.name }
    })
  }, [applicationLifecycle])

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
  const topBarDevelopmentTotals = isViewingActiveVersion
    ? developmentPlanningTotals(
      developmentPlanningPages, developmentPlanningApiContracts,
      applicationLifecycle?.developmentArtifacts
    )
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

  // 生成期间用户是否已经动过输入框。生成是异步的，用户可能在模型返回前就先打字；
  // 那种情况下不能拿模型结果覆盖他写的内容。
  const descriptionEditedRef = useRef(false)

  // 变更说明的手写入口：标记已编辑，之后模型返回不再覆盖。
  const handleDescriptionChange = useCallback((value: string): void => {
    descriptionEditedRef.current = true
    setPublishDescription(value)
  }, [])

  /** 弹窗打开后按本次改动生成变更说明，预填进输入框。
   *
   * 不阻塞弹窗：先照旧打开，生成结果回来了再填。生成不出来（空串）或请求失败都保持
   * 空框让用户自己写 —— 生成只是便利，不能把用户挡在提交之外。
   */
  const requestDescriptionDraft = useCallback((workspaceRoot: string): void => {
    if (!workspaceRoot) return
    setDescriptionGenerating(true)
    void summarizeVersionControl(workspaceRoot)
      .then((generated) => {
        if (generated && !descriptionEditedRef.current) {
          setPublishDescription(generated)
        }
      })
      .catch(() => {
        // 生成失败静默降级：留空框即可，不额外弹提示打扰提交流程。
      })
      .finally(() => setDescriptionGenerating(false))
  }, [])

  // 打开提交并推送弹框。
  const handleOpenPublish = useCallback((): void => {
    setPublishDescription('')
    setVersionGenerating(null)
    setPublishModalOpen(true)
    descriptionEditedRef.current = false
    requestDescriptionDraft(workspaceApplication.workspaceRoot || '')
  }, [requestDescriptionDraft, workspaceApplication.workspaceRoot])

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
      descriptionEditedRef.current = false
      requestDescriptionDraft(workspaceApplication.workspaceRoot || '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionReleasable])

  // 确认提交并推送：调后端真实 git 提交并推送到当前分支，三步进度实时推进。
  // 分支不锁定；提交时冻结当前 lifecycle、资产快照与提交事实，并持久化到 application.json。
  const handleGenerateVersion = useCallback(async (): Promise<void> => {
    if (!viewedBranch) return
    const branchName = viewedBranch.name
    // 前缀 + 空格 + 描述拼成完整提交说明：前缀含看板编号（如 Z000000-0000 #comment chore:），
    // 后端 ensure_board_number 检测到已带编号时幂等返回，不会重复补，所以这里直接拼好。
    // 描述本身可能为空（用户只填前缀就提交），用 filter 防空白再 join，避免出现双空格。
    const description = [publishPrefix.trim(), publishDescription.trim()]
      .filter(Boolean)
      .join(' ')
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
          ? `本次改动已提交至 ${repoUrl} 的版本 ${branchName}，可以继续在该版本上开发。`
          : `本次改动已提交到版本 ${branchName}，可以继续在该版本上开发。`,
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
    publishPrefix,
    viewedBranch,
    workspaceApplication
  ])

  // 确认发起新迭代：回到需求分析阶段。固定新建一条分支（便于回退）。
  // 调后端清空 .devagentstudio 规划产物（保留 AGENTS.md），重置 lifecycle 为 collecting_requirement。
  const handleConfirmIteration = useCallback(async (): Promise<void> => {
    if (!viewedBranch || !applicationLifecycle) return
    const targetBranchName = iterationChoice.branchName.trim()
    setIterationModalOpen(false)
    setSwitchingTargetLabel(targetBranchName)
    try {
      // 1. 新建分支时必须**先**建分支再清空产物 —— 新分支要指向当前这次已提交的代码，
      //    晚于清空就会把新分支建在残缺的树上。后端会切到新分支并推送到远端。
      let branchCreationFailed = false
      const created = await createRepositoryBranch({
        workspaceRoot: workspaceApplication.workspaceRoot,
        branchName: targetBranchName
      })
      if (created.status !== 'pushed') {
        branchCreationFailed = true
        // 后端消息是完整句子（自带句号），嵌进本句前先去掉句末标点，否则会出现「。。」。
        message.warning(
          `版本 ${targetBranchName} 未提交到远端：${asMessageClause(created.message, '原因未知')}。已切换到该版本，稍后可重试提交。`
        )
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
      // 4. 更新分支表：固定新建分支，追加一条记录并把当前分支指针移过去。
      //    不再支持「在当前分支继续」，因此无需清该分支的 gitRef。
      const existingBranches = workspaceApplication.branches || []
      const nextBranches = [
        ...existingBranches,
        createBranchRecord(targetBranchName, awaitingLifecycle, Date.now())
      ]
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
      catalog.reset()
      setViewingBranchName('')
      autoPublishShownRef.current = false
      setSwitchingTargetLabel(undefined)
      setIterationChoice({ mode: 'new', branchName: '' })
      message.success(
        branchCreationFailed
          ? `已发起新迭代（版本 ${targetBranchName}），但未提交到远端。`
          : `已发起新迭代（版本 ${targetBranchName}），开启新的旅程。`
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

  // 打开「基于此版本迭代」弹框：先一次性拉取最新未提交改动数，决定是否需要
  // 用户选 commit / discard。读取失败按 0 处理 —— 让用户至少能开弹框，而不是
  // 被一次状态检查挡在功能外。
  const handleOpenHistoricalIteration = useCallback(
    (sourceBranchName: string): void => {
      setHistoricalIterationSource(sourceBranchName)
      // 预填基于起点版本自增的建议版本号（跳过已有分支重名），用户可直接采用或修改。
      const existingNames = (workspaceApplication.branches || [])
        .map((branch) => branch?.name || '')
        .filter(Boolean)
      setHistoricalIterationBranchName(suggestNextBranchName(sourceBranchName, existingNames))
      setHistoricalIterationDirtyChoice('discard')
      setHistoricalIterationUncommittedCount(0)
      const workspaceRoot = workspaceApplication.workspaceRoot
      if (!workspaceRoot) return
      void inspectAllVersionControl(workspaceRoot)
        .then((snapshot) => {
          setHistoricalIterationUncommittedCount(snapshot.codePaths.length)
        })
        .catch((error) => {
          console.warn('读取未提交改动数失败，按 0 处理。', error)
        })
    },
    [workspaceApplication.workspaceRoot, workspaceApplication.branches]
  )

  // 关闭「基于此版本迭代」弹框：清掉所有相关草稿。
  const handleCancelHistoricalIteration = useCallback((): void => {
    setHistoricalIterationSource('')
    setHistoricalIterationBranchName('')
    setHistoricalIterationUncommittedCount(0)
    setHistoricalIterationDirtyChoice('discard')
  }, [])

  // 确认「基于此版本迭代」：在工作区干净（无未提交改动，或用户选了 discard）时
  // 以历史分支为起点创建新分支，清空规划产物，重置 lifecycle，把新分支写盘。
  // 与 handleConfirmIteration 的关键差异：起点不是当前 HEAD，而是历史分支的 HEAD。
  // 选 commit 的情况不进入这里 —— 弹框已经把那种选择路由到 handleGoToCommitHistoricalIteration。
  const handleConfirmHistoricalIteration = useCallback(async (): Promise<void> => {
    if (!historicalIterationSource || !applicationLifecycle) return
    const sourceBranchName = historicalIterationSource
    const targetBranchName = historicalIterationBranchName.trim()
    const workspaceRoot = workspaceApplication.workspaceRoot
    if (!workspaceRoot || !targetBranchName) return
    // 起点分支记录：新分支的"上一代"分支（保留它的描述用于分支表新增记录的描述兜底）。
    const sourceBranch = findBranch(workspaceApplication, sourceBranchName)
    const shouldDiscard =
      historicalIterationUncommittedCount > 0 &&
      historicalIterationDirtyChoice === 'discard'
    handleCancelHistoricalIteration()
    setSwitchingTargetLabel(targetBranchName)
    try {
      // 1. 用户选 discard 时先调后端重置工作区（git reset --hard HEAD + git clean -fd）。
      //    这是「丢掉」的语义：未提交改动被永久丢弃，不可恢复。
      if (shouldDiscard) {
        await discardVersionControl({ workspaceRoot })
      }
      // 2. 以历史分支为起点创建新分支并推送到远端。后端 create_local_branch 在
      //    startPoint 不为空时执行 `git checkout -b <branch> <startPoint>`。
      const created = await createRepositoryBranch({
        workspaceRoot,
        branchName: targetBranchName,
        startPoint: sourceBranchName
      })
      const branchCreationFailed = created.status !== 'pushed'
      if (branchCreationFailed) {
        // 后端消息是完整句子（自带句号），嵌进本句前先去掉句末标点，否则会出现「。。」。
        message.warning(
          `版本 ${targetBranchName} 未提交到远端：${asMessageClause(created.message, '原因未知')}。已切换到该版本，稍后可重试提交。`
        )
      }
      // 3. 调后端清空 .devagentstudio 规划产物（保留 AGENTS.md + application.json），
      //    回到设计阶段。语义与 handleConfirmIteration 一致。
      await startIteration({
        workspaceRoot,
        branchName: targetBranchName,
        description: sourceBranch?.description || ''
      })
      // 3.5 清空该工作区的会话历史，避免上一轮对话串入新迭代。
      await window.devAgentStudio?.sessions?.clearWorkspace({ workspaceRoot })
      // 4. 创建全新的 lifecycle（collecting_requirement），继承上一轮**已完成**的产物
      //    进度（与 handleConfirmIteration 同一口径）。
      const inheritedArtifacts =
        sourceBranch?.lifecycle?.developmentArtifacts ??
        applicationLifecycle.developmentArtifacts
      const newLifecycle = await createApplicationLifecycle(
        workspaceApplication,
        randomUUID(),
        inheritedArtifacts
      )
      const awaitingLifecycle: ApplicationLifecycle = {
        ...newLifecycle,
        initialization: { ...newLifecycle.initialization, status: 'awaiting_user' }
      }
      onApplicationLifecycleReset(awaitingLifecycle)
      const newThreadId = awaitingLifecycle.initialization?.threadId
      if (newThreadId) {
        onIterationStarted(workspaceApplication, newThreadId, awaitingLifecycle)
      }
      // 5. 更新分支表：追加新分支记录，并把当前分支指针移过去。
      const existingBranches = workspaceApplication.branches || []
      const nextBranches = [
        ...existingBranches,
        createBranchRecord(targetBranchName, awaitingLifecycle, Date.now())
      ]
      const nextApp: ApplicationConfig = {
        ...workspaceApplication,
        branches: nextBranches,
        branchName: targetBranchName
      }
      setWorkspaceApplication(nextApp)
      await persistApplicationConfig(nextApp)
      // 6. 重置前端规划状态，新迭代从空白开始；viewingBranchName 清空让界面落在
      //    新的当前分支上。
      catalog.reset()
      setViewingBranchName('')
      autoPublishShownRef.current = false
      setSwitchingTargetLabel(undefined)
      message.success(
        branchCreationFailed
          ? `已基于版本 ${sourceBranchName} 创建新版本 ${targetBranchName}，但未提交到远端。`
          : `已基于版本 ${sourceBranchName} 创建新版本 ${targetBranchName}，开启新的旅程。`
      )
    } catch (error) {
      setSwitchingTargetLabel(undefined)
      message.error(error instanceof Error ? error.message : '基于此版本迭代失败')
    }
  }, [
    applicationLifecycle,
    handleCancelHistoricalIteration,
    historicalIterationBranchName,
    historicalIterationDirtyChoice,
    historicalIterationSource,
    historicalIterationUncommittedCount,
    onApplicationLifecycleReset,
    onIterationStarted,
    persistApplicationConfig,
    workspaceApplication
  ])

  // 「审阅并提交」分支：关闭历史迭代弹框，切回当前可编辑分支，再打开提交推送弹框。
  // 用户提交完后回到当前分支，那时 dirty=0，再次点击「基于此版本迭代」会直接进入
  // 建分支流程。
  const handleGoToCommitHistoricalIteration = useCallback((): void => {
    // 当前可编辑分支名：弹框打开时是基于历史分支查看，所以需要主动切回去。
    const activeName = workspaceApplication.branchName || ''
    handleCancelHistoricalIteration()
    if (activeName) {
      setViewingBranchName(activeName)
    }
    // 与 handleOpenPublish 同一入口：重置描述并触发模型预填。
    setPublishDescription('')
    setVersionGenerating(null)
    setPublishModalOpen(true)
    descriptionEditedRef.current = false
    requestDescriptionDraft(workspaceApplication.workspaceRoot || '')
    message.info('请先提交当前版本的改动，提交完成后再回来「基于此版本迭代」。')
  }, [
    handleCancelHistoricalIteration,
    requestDescriptionDraft,
    workspaceApplication.branchName,
    workspaceApplication.workspaceRoot
  ])

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
                returningWelcome={returningWelcome}
                lifecycle={topBarLifecycle}
                developmentTotals={topBarDevelopmentTotals}
                theme={theme}
                onThemeChange={handleThemeChange}
                onPublishBranch={handleOpenPublish}
                onStartIteration={() => {
                  // 固定新建一个版本（便于回退）：打开时基于当前分支给出递增的建议版本号，
                  // 跳过与已有分支的重名。用户可直接采用或自行修改。
                  const existingNames = (workspaceApplication.branches || [])
                    .map((branch) => branch?.name || '')
                    .filter(Boolean)
                  setIterationChoice({
                    mode: 'new',
                    branchName: suggestNextBranchName(viewedBranch?.name || activeBranchName, existingNames)
                  })
                  setIterationModalOpen(true)
                }}
                onBranchSelect={handleBranchSelect}
                onStartHistoricalIteration={handleOpenHistoricalIteration}
                viewingBranchName={viewingBranchName}
              />
              <div className={cx('workbench-shell-body')}>
                <LeftPanel
                  application={workspaceApplication}
                  applicationLifecycle={applicationLifecycle}
                  developmentTotals={topBarDevelopmentTotals}
                  developmentPlanningReady={developmentPlanningPagesLoaded}
                  planningCatalogStatus={catalog.status}
                  onPlanningCatalogRetry={catalog.reload}
                  developmentPlanningPages={developmentPlanningPages}
                  developmentPlanningPageTree={developmentPlanningPageTree}
                  developmentPlanningApiContracts={developmentPlanningApiContracts}
                  developmentPlanningEntities={developmentPlanningEntities}
                  editorMode={editorMode}
                  onApplicationUpdate={handleApplicationUpdate}
                  onPersistApplication={persistApplicationConfig}
                  onPlanningArtifactsRefresh={handlePlanningArtifactsRefresh}
                  previewBaseUrl=""
                  previewLaunchError=""
                  previewLaunchLoading={false}
                  onApplicationLifecycleChange={onApplicationLifecycleChange}
                  onReturnWelcome={onReturnWelcome}
                  onSubmitPlanningClarification={onSubmitPlanningClarification}
                  onStartDesignStageRevision={onStartDesignStageRevision}
                  onStartIterationPlanning={(request, workflowDebug, debugStage) =>
                    onStartIterationPlanning(
                      workspaceApplication.id,
                      request,
                      workflowDebug,
                      debugStage
                    )
                  }
                  onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
                  onPlanningStreamReady={onPlanningStreamReady}
                  onSavePlanningRequirementSpec={onSavePlanningRequirementSpec}
                  onStopPlanning={onStopPlanning}
                  onSessionHistoryReadyChange={handleSessionHistoryReadyChange}
                  generatingTemplate={generatingTemplate}
                  onRetryPlanning={onRetryPlanning}
                  onReconcilePlanning={onReconcilePlanning}
                  onRetryTemplateGeneration={onRetryTemplateGeneration}
                  onRetryTemplateReconcile={onRetryTemplateReconcile}
                  planningState={planningState}
                  theme={theme}
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
              descriptionGenerating={descriptionGenerating}
              onDescriptionChange={handleDescriptionChange}
              publishPrefix={publishPrefix}
              onPrefixChange={setPublishPrefix}
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
              historicalIterationSourceBranch={historicalIterationSource || undefined}
              historicalIterationBranchName={historicalIterationBranchName}
              onHistoricalIterationBranchNameChange={setHistoricalIterationBranchName}
              historicalIterationUncommittedCount={historicalIterationUncommittedCount}
              historicalIterationDirtyChoice={historicalIterationDirtyChoice}
              onHistoricalIterationDirtyChoiceChange={setHistoricalIterationDirtyChoice}
              onCancelHistoricalIteration={handleCancelHistoricalIteration}
              onConfirmHistoricalIteration={handleConfirmHistoricalIteration}
              onGoToCommitHistoricalIteration={handleGoToCommitHistoricalIteration}
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

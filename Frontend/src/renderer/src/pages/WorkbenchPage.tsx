import { Layout, message, notification } from 'antd'
import { randomUUID } from '@ag-ui/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { LeftPanel, WorkbenchTopBar } from '../components'
import WorkbenchVersionModals from '../components/WorkbenchVersionModals'
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
  currentVersion,
  createInitialVersion,
  createIterationVersion,
  createRollbackVersion,
  findVersion,
  isVersionReleasable,
  isViewingHistoricalVersion,
  releaseVersion
} from '../service/applicationVersions'
import { publishVersion } from '../service/versionPublish'
import { startIteration } from '../service/iterationService'
import type {
  RequirementSpecDraftSaveResult,
  WorkflowRevisionContinuationHandoff
} from '../service/applicationPagePlanning'
import type { ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationVersion,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
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
  // —— 应用版本（生成新版本/发起新迭代/基于此版本迭代）状态 ——
  // 当前查看的版本 id；为空时取版本链头（currentVersionId）。
  const [viewingVersionId, setViewingVersionId] = useState<string>('')
  // 生成版本弹框：发布中三步进度（打包/提交码云/打Tag），null 表示未在生成。
  const [versionGenerating, setVersionGenerating] = useState<{ stepIndex: number } | null>(null)
  // 生成版本弹框：版本说明草稿。
  const [publishDescription, setPublishDescription] = useState('')
  // 生成版本弹框是否开启。
  const [publishModalOpen, setPublishModalOpen] = useState(false)
  // 发起新迭代弹框是否开启。
  const [iterationModalOpen, setIterationModalOpen] = useState(false)
  // 基于此版本迭代（回退）弹框的目标版本 id。
  const [rollbackTargetVersionId, setRollbackTargetVersionId] = useState<string | undefined>(
    undefined
  )
  // 版本切换全屏加载的目标标签；为空时不显示。
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
          // 保留前端初始化的版本链，避免 application.json 无 versions 字段时覆盖丢失。
          versions: prev.versions,
          currentVersionId: prev.currentVersionId
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
    // 仅当应用 id 变化时才用 prop 覆盖，避免发起新迭代后 lifecycle 变化触发 effect 回退版本。
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

  // 应用首次进入工作台时初始化首个版本（iterating），标签取自表单 versionNo。
  // 已有 versions 的应用（如发起新迭代后重新进入）跳过，保留版本链。
  useEffect(() => {
    if (!applicationLifecycle) return
    setWorkspaceApplication((prev) => {
      if (prev.versions && prev.versions.length > 0) return prev
      const versionNo = prev.versionNo || 'v1.0'
      const initialVersion = createInitialVersion(
        prev.id,
        versionNo,
        applicationLifecycle,
        Date.now()
      )
      return { ...prev, versions: [initialVersion], currentVersionId: initialVersion.id }
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

  // —— 应用版本操作回调 ——
  // 当前查看版本（viewingVersionId 为空时取版本链头）。
  const activeVersionId = workspaceApplication.currentVersionId || ''
  const viewedVersion =
    findVersion(workspaceApplication, viewingVersionId) || currentVersion(workspaceApplication)
  const isViewingActiveVersion = viewedVersion?.id === activeVersionId
  // 已发布版本或查看非活跃版本时锁定阶段切换，只能回看。
  const versionLocked = !isViewingActiveVersion || viewedVersion?.status === 'released'
  // 是否在回看历史版本（非活跃版本）。顶部栏与内容区共用这一个口径：
  // 不能复用 versionLocked —— 它把"当前版本已发布"也算作锁定，那是阶段不可点的语义。
  const viewingHistoricalVersion = isViewingHistoricalVersion(activeVersionId, viewedVersion?.id)
  // 当前活跃迭代版本是否可发布（验收通过等条件齐全）。
  const releaseVersionTarget = isViewingActiveVersion ? viewedVersion : undefined
  const versionReleasable = Boolean(
    releaseVersionTarget &&
      isVersionReleasable({
        ...releaseVersionTarget,
        lifecycle: applicationLifecycle || releaseVersionTarget.lifecycle
      })
  )
  const autoPublishShownRef = useRef(false)

  // 打开生成版本弹框。
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

  // 确认生成版本：调后端真实 git 提交/打Tag/推送，三步进度实时推进，完成后版本转 released 并锁定。
  // 发布时冻结当前 lifecycle、页面资产快照进版本，并持久化到 application.json。
  const handleGenerateVersion = useCallback(async (): Promise<void> => {
    if (!viewedVersion) return
    const targetVersionId = viewedVersion.id
    const versionLabel = viewedVersion.versionLabel
    const description = publishDescription.trim()
    setVersionGenerating({ stepIndex: 0 })
    try {
      const result = await publishVersion(
        {
          workspaceRoot: workspaceApplication.workspaceRoot,
          repoUrl: workspaceApplication.repoUrl,
          versionLabel,
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
      const target = findVersion(workspaceApplication, targetVersionId)
      if (!target) {
        setVersionGenerating(null)
        return
      }
      const released = releaseVersion(target, description, now, {
        commitSha: result.commitSha!,
        tag: result.tag!,
        committedAt: now
      })
      // 冻结发布时刻的 lifecycle 与资产快照，回看历史版本时停在发布时刻。
      const frozen: ApplicationVersion = {
        ...released,
        ...(frozenLifecycle ? { lifecycle: frozenLifecycle } : {}),
        artifactSummary: {
          pageIds: workspaceApplication.pages,
          deployableScript: `deploy-${versionLabel}.sh`
        },
        snapshot: { pageIds: workspaceApplication.pages }
      }
      const nextApplication: ApplicationConfig = {
        ...workspaceApplication,
        versions: (workspaceApplication.versions || []).map((v) =>
          v.id === frozen.id ? frozen : v
        )
      }
      setWorkspaceApplication(nextApplication)
      // 持久化到 application.json，刷新后版本状态不丢失。
      try {
        await saveWorkspaceApplicationConfig(nextApplication.workspaceRoot, nextApplication)
      } catch (saveError) {
        console.warn('保存 application.json 失败，版本状态仅保留在内存。', saveError)
      }
      setVersionGenerating(null)
      setPublishModalOpen(false)
      const repoUrl = String(workspaceApplication.repoUrl || '').trim()
      notification.success({
        message: '版本已生成',
        description: repoUrl
          ? `${versionLabel} 已提交至 ${repoUrl} 并打 Tag，锁定为只读版本，可发起新迭代继续开发。`
          : `${versionLabel} 已打包提交并打 Tag，锁定为只读版本，可发起新迭代继续开发。`,
        placement: 'bottomRight',
        duration: 4
      })
    } catch (error) {
      setVersionGenerating(null)
      message.error(error instanceof Error ? error.message : '生成版本失败')
    }
  }, [applicationLifecycle, publishDescription, viewedVersion, workspaceApplication])

  // 确认发起新迭代：基于当前版本派生下一版本（minor+1），回到需求分析阶段。
  // 调后端清空 .xcodeagent 规划产物（保留 AGENTS.md），重置 lifecycle 为 collecting_requirement。
  const handleConfirmIteration = useCallback(async (): Promise<void> => {
    if (!viewedVersion || !applicationLifecycle) return
    const parentVersionId = viewedVersion.id
    const versionLabel = viewedVersion.versionLabel
    setIterationModalOpen(false)
    setSwitchingTargetLabel(versionLabel)
    const parent = findVersion(workspaceApplication, parentVersionId)
    if (!parent) {
      setSwitchingTargetLabel(undefined)
      return
    }
    try {
      // 1. 调后端清空 .xcodeagent 规划产物（保留 AGENTS.md + application.json）。
      await startIteration({
        workspaceRoot: workspaceApplication.workspaceRoot,
        versionLabel,
        description: viewedVersion.description || ''
      })
      // 1.5 清空环境数据目录中该工作区的全部会话历史，
      // 避免上一版本的对话卡片串入新迭代。
      await window.xcodeAgent?.sessions?.clearWorkspace({
        workspaceRoot: workspaceApplication.workspaceRoot
      })
      // 2. 创建全新的 lifecycle（collecting_requirement），revision 从 1 重新开始。
      //    继承上一版本**已完成**的产物进度：新迭代保留已有工程代码，那些产物仍然存在，
      //    不该重新变回"未开发"——否则"全部产物完成"的测试门禁在增量迭代里永远满足不了
      //    （本轮构建范围只覆盖改动的产物，不会再去开发其余已完成的产物）。
      const inheritedArtifacts =
        parent.lifecycle?.developmentArtifacts ?? applicationLifecycle.developmentArtifacts
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
      // 2.5 登记 planning state（restoreArtifactsFromDisk=false 避免 reconcile 触发自动分析），
      // 让用户输入需求后能触发 planning workflow。
      const newThreadId = awaitingLifecycle.initialization?.threadId
      if (newThreadId) {
        onIterationStarted(workspaceApplication, newThreadId, awaitingLifecycle)
      }
      // 3. 派生新版本，lifecycle 用全新的 collecting_requirement。
      // 3. 派生新版本，lifecycle 用全新的 collecting_requirement。
      const next = createIterationVersion(
        workspaceApplication.id,
        parent,
        awaitingLifecycle,
        Date.now()
      )
      const nextApp: ApplicationConfig = {
        ...workspaceApplication,
        versions: [...(workspaceApplication.versions || []), next],
        currentVersionId: next.id
      }
      setWorkspaceApplication(nextApp)
      // 4. 持久化 application.json（版本链更新）。
      await saveWorkspaceApplicationConfig(nextApp.workspaceRoot, nextApp)
      // 5. 阶段覆盖与浏览进度均按版本作用域持久化，新版本天然从生命周期重新推导，
      //    无需再手动清除上一迭代遗留的覆盖。
      // 6. 重置前端规划状态，新迭代从空白开始。
      setDevelopmentPlanningPages([])
      setDevelopmentPlanningPageTree([])
      setDevelopmentPlanningApiContracts([])
      setDevelopmentPlanningEntities([])
      setViewingVersionId('')
      autoPublishShownRef.current = false
      setSwitchingTargetLabel(undefined)
      message.success('已发起新迭代，已回到需求分析阶段。')
    } catch (error) {
      setSwitchingTargetLabel(undefined)
      message.error(error instanceof Error ? error.message : '发起新迭代失败')
    }
  }, [
    applicationLifecycle,
    viewedVersion,
    workspaceApplication,
    onApplicationLifecycleReset,
    onIterationStarted
  ])

  // 确认基于历史版本迭代（回退）：以历史版本内容派生新的顺序版本。
  // 重置规划产物状态，让新迭代从需求收集重新开始。
  const handleConfirmRollback = useCallback((): void => {
    if (!rollbackTargetVersionId) return
    const targetId = rollbackTargetVersionId
    const headId = activeVersionId
    setRollbackTargetVersionId(undefined)
    setSwitchingTargetLabel(undefined)
    const head = findVersion(workspaceApplication, headId)
    const restored = findVersion(workspaceApplication, targetId)
    if (!head || !restored) return
    const next = createRollbackVersion(workspaceApplication.id, head, restored, Date.now())
    const nextApp: ApplicationConfig = {
      ...workspaceApplication,
      versions: [...(workspaceApplication.versions || []), next],
      currentVersionId: next.id
    }
    setWorkspaceApplication(nextApp)
    // 重置规划状态，新迭代从需求收集重新开始。
    setDevelopmentPlanningPages([])
    setDevelopmentPlanningPageTree([])
    setDevelopmentPlanningApiContracts([])
    setDevelopmentPlanningEntities([])
    setViewingVersionId('')
    autoPublishShownRef.current = false
    void saveWorkspaceApplicationConfig(nextApp.workspaceRoot, nextApp).catch((saveError) => {
      console.warn('保存 application.json 失败。', saveError)
    })
    message.success('已基于历史版本生成新迭代版本。')
  }, [activeVersionId, rollbackTargetVersionId, workspaceApplication])

  // 切换查看版本：展示切换加载层后切到目标版本。
  const handleVersionSelect = useCallback(
    (versionId: string): void => {
      const target = findVersion(workspaceApplication, versionId)
      if (!target) return
      setSwitchingTargetLabel(target.versionLabel)
      window.setTimeout(() => {
        setViewingVersionId(versionId)
        setSwitchingTargetLabel(undefined)
      }, 600)
    },
    [workspaceApplication]
  )

  return (
    <Layout className={cx('workbench-shell')} data-theme={theme}>
      {developmentPlanningPagesLoaded ? (
        <WorkbenchPhaseProvider
          key={viewedVersion?.id || workspaceApplication.id}
          applicationId={workspaceApplication.id}
          versionId={viewedVersion?.id || workspaceApplication.id}
          lifecycle={isViewingActiveVersion ? applicationLifecycle : viewedVersion?.lifecycle}
          locked={versionLocked}
        >
          <UncommittedChangesProvider
            workspaceRoot={
              workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
            }
          >
            <div className={cx('workbench-shell-column')}>
              <WorkbenchTopBar
                application={workspaceApplication}
                workspaceRoot={
                  workspaceApplication.workspaceRoot || workspaceApplication.projectParentPath || ''
                }
                onReturnWelcome={onReturnWelcome}
                lifecycle={applicationLifecycle}
                rightPanelOpen={rightPanelOpen}
                onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
                onPublishVersion={handleOpenPublish}
                onRollbackVersion={(versionId) => setRollbackTargetVersionId(versionId)}
                onStartIteration={() => setIterationModalOpen(true)}
                onVersionSelect={handleVersionSelect}
                viewingVersionId={viewingVersionId}
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
                  // 只读双 tab 只针对回看历史版本。不能复用 versionLocked：它把 released
                  // 也算作锁定（阶段不可点），而当前版本发布后正是 released，会误把活跃
                  // 版本也换成只读视图，用户就看不到当前应用的执行情况了。
                  versionReadOnly={viewingHistoricalVersion}
                  // 该版本的发布 tag：历史版本的应用文件按它读取当时的内容。
                  viewedVersionTag={viewedVersion?.gitRef?.tag}
                />
              </div>
            </div>
            <WorkbenchVersionModals
              application={workspaceApplication}
              publishVersionLabel={
                publishModalOpen && viewedVersion ? viewedVersion.versionLabel : undefined
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
              iterationBaseVersionId={iterationModalOpen ? activeVersionId : undefined}
              onCancelIteration={() => setIterationModalOpen(false)}
              onConfirmIteration={handleConfirmIteration}
              rollbackTargetVersionId={rollbackTargetVersionId}
              activeVersionId={activeVersionId}
              onCancelRollback={() => setRollbackTargetVersionId(undefined)}
              onConfirmRollback={handleConfirmRollback}
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

import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench, useWorkbenchPhase } from '../../context'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  ApplicationDevelopmentTask,
  ApplicationMenuItem,
  EditorMode,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import { CLASS_PREFIX, composePreviewUrl, cx, openPreviewWindow, previewOrigin } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import MessageList from './components/MessageList'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import PageContextHeader from './components/PageContextHeader'
import type { PageContextStatus } from './components/PageContextHeader'
import DocPanel from './components/DocPanel'
import SourcePanel from './components/SourcePanel'
import {
  buildBuildTaskPlanDoc,
  buildEndpointDesignDoc,
  buildPageDesignDoc,
  buildPageSource,
  buildProjectPlanDoc,
  buildRequirementSpecDoc,
  type PageDesign
} from '../../workbenchArtifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { isInitialPlanningPhase } from '../../workbenchPhase'
import type { WorkbenchPhase } from '../../workbenchPhase'
import RightPanelTabs, { type WorkspaceTab, type WorkspaceTabKey } from './components/RightPanelTabs'
import PlanExecutionDock from './components/PlanExecutionDock'
import SessionSidebar from './components/SessionSidebar'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { chatCopy } from './constants'
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  requiresEndpointDetailDesign,
  requiresInitialDetailDesignSelection,
  requiresPageDetailDesign,
  sessionDetailTargetKey,
  workflowDetailTargetKey,
  type WorkflowPreviewTarget
} from './utils'
import {
  deriveDisplayedPlanExecutionMode,
  planExecutionShowsDebugResume,
  planExecutionContextForEndpoint,
  planExecutionContextForPage,
  shouldRenderPlanExecutionDock,
  workflowResumeNode,
  type PlanExecutionMode
} from './planExecutionMode'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentPlanningReady: boolean
  hasPageDesigns: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningPageTree: DevelopmentPlanningPageTreeNode[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  previewBaseUrl: string
  previewLaunchError: string
  rightPanelOpen: boolean
  onRightPanelOpenChange: (open: boolean) => void
  theme: 'light' | 'dark'
}

type ActiveView = 'chat' | 'skills' | 'files' | 'settings'

type ActiveApiEndpointTarget = {
  apiContractId: string
  endpointId: string
  endpointKey: string
  label: string
}

type ActiveDetailTarget =
  | { type: 'none' }
  | { type: 'page'; pageId: string }
  | ({ type: 'endpoint' } & ActiveApiEndpointTarget)

/** 为页面或接口生成稳定的前端目标键，隔离各目标的临时交互状态。 */
function detailTargetKey(target: ActiveDetailTarget): string {
  if (target.type === 'page') return pageDetailTargetKey(target.pageId)
  if (target.type === 'endpoint') {
    return endpointDetailTargetKey(target.apiContractId, target.endpointId)
  }
  return ''
}

// 设计阶段产物文档的可用进度（按生命周期 initialization.stage 判定）。
// 顺序与 planning.ts / workbenchPhase 的规划期 stage 推进一致；到达门槛 stage 后对应文档才生成。
const DESIGN_DOC_STAGE_ORDER = [
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation',
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_build_task_plan',
  'generating_application_template_files',
  'ready_for_workbench'
]
function designStageReached(stage: string | undefined, threshold: string): boolean {
  if (!stage) return false
  const current = DESIGN_DOC_STAGE_ORDER.indexOf(stage)
  const target = DESIGN_DOC_STAGE_ORDER.indexOf(threshold)
  return current >= 0 && target >= 0 && current >= target
}

/** 设计阶段三份产物文档的 key → 可用的门槛 stage。 */
const DESIGN_DOC_THRESHOLDS: Record<WorkspaceDocKey, string> = {
  'requirement-spec': 'generating_requirement_spec',
  'project-plan': 'generating_project_plan',
  'build-task-plan': 'generating_build_task_plan'
}

/** 当前 stage 对应"最新就绪"的文档 key：生成完成(stage 推进)自动跟随到对应文档。 */
function designActiveDocKey(stage: string | undefined): WorkspaceDocKey | undefined {
  if (designStageReached(stage, 'generating_build_task_plan')) return 'build-task-plan'
  if (designStageReached(stage, 'generating_project_plan')) return 'project-plan'
  if (designStageReached(stage, 'generating_requirement_spec')) return 'requirement-spec'
  return undefined
}

const WORKSPACE_DOC_KEYS: ReadonlySet<string> = new Set(['requirement-spec', 'project-plan', 'build-task-plan'])

/** 判断 Workflow 是否已经返回详细设计确认卡片，避免外层选择器遮住待确认内容。 */
function workflowHasDetailReview(workflow: unknown): boolean {
  if (!workflow || typeof workflow !== 'object') return false
  const payload = workflow as {
    events?: Array<{ data?: Record<string, unknown> }>
    result?: Record<string, unknown>
    state?: Record<string, unknown>
    summary?: Record<string, unknown>
  }
  return [
    payload.summary?.clarification,
    payload.state?.clarification,
    payload.result?.clarification,
    ...(payload.events || []).map((event) => {
      const detail = event.data?.detail
      return detail && typeof detail === 'object'
        ? (detail as Record<string, unknown>).clarification
        : undefined
    })
  ].some(
    (clarification) =>
      clarification &&
      typeof clarification === 'object' &&
      (clarification as Record<string, unknown>).mode === 'detail_review'
  )
}

/** 从当前消息历史里读取最后一个 Workflow，弥补 activeWorkflow 在运行结束瞬间的状态空窗。 */
function latestMessageWorkflow(messages: Array<{ workflow?: unknown }>): unknown {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].workflow) return messages[index].workflow
  }
  return undefined
}

/** 按页面名称递归查找对应的菜单配置。 */
function findPageMenuItem(
  items: ApplicationMenuItem[],
  label: string
): ApplicationMenuItem | undefined {
  for (const item of items) {
    if (item.label === label) return item
    const matchedChild = findPageMenuItem(item.children || [], label)
    if (matchedChild) return matchedChild
  }
  return undefined
}

/** 根据页面设计、开发任务和当前执行态生成顶部上下文栏的可信状态。 */
function pageContextStatus(
  designed: boolean,
  tasks: ApplicationDevelopmentTask[],
  mode: PlanExecutionMode,
  targetType: 'page' | 'api',
  taskSummary?: DevelopmentPlanningPageOption['taskSummary']
): PageContextStatus {
  const targetLabel = targetType === 'api' ? 'API 设计' : '页面设计'
  const totalTasks = taskSummary?.total || tasks.length
  const completedTasks =
    taskSummary?.completed ?? tasks.filter((task) => task.status === 'completed').length
  const runningTasks =
    taskSummary?.running ?? tasks.filter((task) => task.status === 'in_progress').length
  const details = [
    `${targetLabel}${designed ? '已完成' : '尚未完成'}`,
    totalTasks > 0 ? `开发任务 ${completedTasks} / ${totalTasks}` : '开发计划暂未拆分'
  ]

  if (mode === 'running' || mode === 'stopping') {
    return { details, label: mode === 'stopping' ? '停止中' : '执行中', tone: 'active' }
  }
  if (
    mode === 'awaiting_authorization' ||
    mode === 'awaiting_repair_confirmation' ||
    mode === 'awaiting_acceptance' ||
    mode === 'awaiting_plan_adjustment'
  ) {
    return { details, label: '待确认', tone: 'warning' }
  }
  if (mode === 'failed') return { details, label: '失败', tone: 'error' }
  if (mode === 'stopped') return { details, label: '已停止', tone: 'neutral' }
  if (!designed) return { details, label: '待设计', tone: 'neutral' }
  if (totalTasks > 0 && completedTasks === totalTasks) {
    return { details, label: '已完成', tone: 'success' }
  }
  if (runningTasks > 0 || completedTasks > 0) {
    return { details, label: '开发中', tone: 'active' }
  }
  return { details, label: '已设计', tone: 'success' }
}

/** 在最新 ProjectPlan 页面目录中解析会话保存的页面标识，避免旧 pageId 覆盖当前选择。 */
function resolvePlanningPageId(pages: DevelopmentPlanningPageOption[], pageId: string): string {
  const normalizedPageId = pageId.trim()
  if (!normalizedPageId) return ''
  const matched = pages.find((page) => page.pageId === normalizedPageId)
  if (matched) return matched.pageId
  const alias = pageIdAlias(normalizedPageId)
  return pages.find((page) => pageIdAlias(page.pageId) === alias)?.pageId || ''
}

/** 生成页面标识的宽松别名，兼容历史会话里的 page- 前缀差异。 */
function pageIdAlias(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/^page-/, '')
}

/** 组织应用侧栏、对话区、页面信息与预览面板的主工作台。 */
export default function AiChatPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
  hasPageDesigns,
  developmentPlanningPages,
  developmentPlanningPageTree,
  developmentPlanningApiContracts,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  rightPanelOpen,
  onRightPanelOpenChange,
  theme
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  // 设计阶段文档编辑态:editedDesignDocs 存保存后的编辑版(覆盖静态产物显示);
  // 编辑草稿由 DocPanel 内部管理(默认即编辑,IDE 式),保存时经 onSaveEdit(draft) 回传。
  const [editedDesignDocs, setEditedDesignDocs] = useState<Partial<Record<WorkspaceDocKey, string>>>({})
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  const [interactingDetailTargetKey, setInteractingDetailTargetKey] = useState('')
  const [generatingDetailTargetKey, setGeneratingDetailTargetKey] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [elementInspectionActive, setElementInspectionActive] = useState(false)
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  const { publishAiMessage } = useWorkbench()
  // 阶段门禁：仅研发阶段可编辑页面/接口 spec；产品/测试阶段下设计入口锁定。
  const { phase: activeWorkbenchPhase } = useWorkbenchPhase()
  // 按当前应用工作区取演示数据（三应用各自独立）。
  const scenario = appDataByWorkspace(application.workspaceRoot)
  // 设计阶段：右侧工作区只显示「文档」tab（spec 文档），预览/源码为开发阶段产物。
  const isDesignPhase = activeWorkbenchPhase === 'product'
  const {
    assistantPanelWidth,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    setRightPanel,
    splitDragging
  } = useAssistantPreviewLayout({ rightPanelOpen })
  const activePageId = activeDetailTarget.type === 'page' ? activeDetailTarget.pageId : ''
  const activeApiEndpoint = activeDetailTarget.type === 'endpoint' ? activeDetailTarget : undefined
  const activeTargetKey = detailTargetKey(activeDetailTarget)
  const activePageOption = useMemo(
    () => developmentPlanningPages.find((page) => page.pageId === activePageId),
    [activePageId, developmentPlanningPages]
  )
  const activePreviewPath = activePageOption?.path || '/'
  const directModificationEnabled = activeApiEndpoint
    ? developmentPlanningApiContracts.some((contract) =>
        contract.endpoints.some((endpoint, endpointIndex) => {
          const endpointId = endpoint.id || String(endpointIndex + 1)
          const apiContractId = endpoint.apiContractId || contract.id
          return (
            apiContractId === activeApiEndpoint.apiContractId &&
            endpointId === activeApiEndpoint.endpointId &&
            Boolean(endpoint.designed || endpoint.hasDetailPlan)
          )
        })
      )
    : Boolean(activePageOption?.designed || activePageOption?.hasDetailPlan)

  /** 接收实时 launch 结果并复用手动预览入口打开右侧面板。 */
  const handlePreviewReady = useCallback(
    (target: WorkflowPreviewTarget) => {
      if (handledPreviewTargetRef.current === target.key) return
      handledPreviewTargetRef.current = target.key
      const nextBaseUrl = previewOrigin(target.url)
      const nextPreviewUrl = composePreviewUrl(nextBaseUrl, activePreviewPath)
      if (!nextPreviewUrl) return
      setPreviewError('')
      setRuntimePreviewBaseUrl(nextBaseUrl)
      setRuntimePreviewLaunchError('')
      setRightPanel({ type: 'preview', requestKey: target.key, url: nextPreviewUrl })
    },
    [activePreviewPath, setRightPanel]
  )

  // 同步工作台自动启动返回的最新前端端口和错误，不进行任何浏览器持久化。
  useEffect(() => {
    setRuntimePreviewBaseUrl(previewOrigin(previewBaseUrl))
    setRuntimePreviewLaunchError(previewLaunchError)
  }, [previewBaseUrl, previewLaunchError])

  const {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createEndpointSession,
    createPageSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleSelectEndpoint,
    handleSelectPage,
    loadingSessions,
    messages,
    persistSession,
    runningSessionsRef,
    selectedSkills,
    sessionError,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({
    application,
    editorMode,
    onCloseRightPanel: () => setRightPanel(undefined)
  })

  const {
    activeWorkflow,
    directModificationRunning,
    error,
    handleAcceptPreview,
    handleAdjustPlan,
    handleEndPlan,
    handleResumePlan,
    handleRetryPlan,
    handleStopPlan,
    handleSend,
    handleStartEndpointDetailConfirmation,
    handleStartDetailConfirmation,
    handleStopGenerating,
    handleSubmitClarification,
    loading,
    sessionRunStates,
    stopping,
    workspaceBusy
  } = useWorkflowConversation({
    activeSession,
    agUiSessionsRef,
    application,
    applicationLifecycle,
    draft,
    draftKey,
    editorMode,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
    getSessionMessages,
    persistSession,
    onApplicationLifecycleChange,
    onPreviewReady: handlePreviewReady,
    publishAiMessage,
    runningSessionsRef,
    selectedApiContractId: activeApiEndpoint?.apiContractId,
    selectedEndpointId: activeApiEndpoint?.endpointId,
    selectedEndpointLabel: activeApiEndpoint?.label,
    selectedSkills,
    selectedPageId: activePageOption?.pageId || activePageOption?.key,
    selectedPageLabel: activePageOption?.label,
    directModificationEnabled,
    designPhase: isDesignPhase,
    autoStartDesign: isInitialPlanningPhase(applicationLifecycle),
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  })
  const { requestCodeChangeRevert, revertingCodeChangeIds } = useCodeChangeRevert({
    activeSession,
    disabled: loading || workspaceBusy,
    getSessionMessages,
    persistSession,
    rightPanel,
    setRightPanel,
    setSessionMessages
  })

  const copy = chatCopy[editorMode]
  const workflowIdentity = {
    runId: activeWorkflow?.runId,
    threadId: activeWorkflow?.threadId || activeSession?.threadId
  }
  const targetExecutionContext = activeApiEndpoint
    ? planExecutionContextForEndpoint(
        applicationLifecycle,
        activeApiEndpoint.apiContractId,
        activeApiEndpoint.endpointId,
        workflowIdentity
      )
    : planExecutionContextForPage(
        applicationLifecycle,
        activePageOption?.pageId || activePageId,
        workflowIdentity
      )
  const scopedExecution = targetExecutionContext.execution
  const displayedPlanExecutionMode = deriveDisplayedPlanExecutionMode(
    scopedExecution,
    stopping ? 'stopping' : activeWorkflow?.summary.status,
    loading,
    Boolean(applicationLifecycle)
  )
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const activePageTitle =
    activePageOption?.label || application.defaultPage || application.pages[0] || '页面'
  const activePage = useMemo(
    () => findPageMenuItem(application.menus?.items || [], activePageTitle),
    [activePageTitle, application.menus?.items]
  )
  const activeApiEndpointOption = useMemo(() => {
    if (!activeApiEndpoint) return undefined
    for (const contract of developmentPlanningApiContracts) {
      for (const [endpointIndex, endpoint] of contract.endpoints.entries()) {
        const endpointId = endpoint.id || String(endpointIndex + 1)
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointKey = `${apiContractId}:${endpointId}`
        if (
          endpointKey === activeApiEndpoint.endpointKey ||
          (apiContractId === activeApiEndpoint.apiContractId &&
            endpointId === activeApiEndpoint.endpointId)
        ) {
          return { contract, endpoint, endpointId, endpointKey, apiContractId }
        }
      }
    }
    return undefined
  }, [activeApiEndpoint, developmentPlanningApiContracts])
  const activeHeaderTarget = activeApiEndpoint
    ? {
        type: 'api' as const,
        title: activeApiEndpoint.label,
        path:
          activeApiEndpointOption?.endpoint.path ||
          activeApiEndpoint.label.replace(/^[A-Z]+\s+/, ''),
        description:
          activeApiEndpointOption?.endpoint.summary ||
          `接口来自 ${activeApiEndpointOption?.contract.label || activeApiEndpoint.apiContractId}`,
        keyFeatures: [
          `Method：${activeApiEndpointOption?.endpoint.method || activeApiEndpoint.label.split(' ')[0] || 'API'}`,
          `Contract：${activeApiEndpointOption?.contract.label || activeApiEndpoint.apiContractId}`,
          activeApiEndpointOption?.endpoint.designed ||
          activeApiEndpointOption?.endpoint.hasDetailPlan
            ? '状态：已设计'
            : '状态：待设计'
        ]
      }
    : {
        type: 'page' as const,
        title: activePageTitle,
        path: activePageOption?.path || activePage?.path || '/',
        description:
          activePageOption?.purpose || activePage?.purpose || application.senario || '当前应用页面',
        keyFeatures: activePage?.keyFeatures || []
      }
  const activeHeaderStatus = pageContextStatus(
    activeApiEndpoint
      ? Boolean(
          activeApiEndpointOption?.endpoint.designed ||
            activeApiEndpointOption?.endpoint.hasDetailPlan
        )
      : Boolean(
          activePageOption?.designed || activePageOption?.hasDetailPlan || activePage?.design
        ),
    activeApiEndpoint ? [] : activePage?.developmentTasks || [],
    displayedPlanExecutionMode,
    activeHeaderTarget.type,
    activeApiEndpoint ? undefined : activePageOption?.taskSummary
  )
  const latestWorkflowForDisplay = activeWorkflow || latestMessageWorkflow(messages)
  // 右侧工作区：已设计完成的页面 → 默认预览（手动关闭后不再抢开，直到切换目标）。
  const previewTabUrl = composePreviewUrl(runtimePreviewBaseUrl, activeHeaderTarget.path)
  // 自动开预览：切换目标页 → 重新自动开；用户手动关闭后不再抢开（dismissed），直到切换页面。
  const autoOpenStateRef = useRef<{
    page: string
    dismissed: boolean
    type: WorkspaceTabKey | null
  }>({ page: '', dismissed: false, type: null })
  // 记录当前右侧面板归属的页面，页面切换时强制刷新面板内容（未设计页空占位）。
  const lastPanelForPageRef = useRef('')
  useEffect(() => {
    const state = autoOpenStateRef.current
    if (state.page !== activePageId) {
      state.page = activePageId
      state.dismissed = false
      state.type = null
    }
    if (state.dismissed) return
    // 设计阶段：右侧固定「文档」区，自动落到第一份已生成产物（需求文档/项目计划/构建任务，tab 渐进可用）。
    // 注意：本 effect 依赖 rightPanel，必须仅在非 doc 或未选中有效文档时才 set，否则每次新建对象 →
    // rightPanel 引用变 → effect 重跑 → 再 set，形成 Maximum update depth 死循环。
    if (isDesignPhase) {
      state.type = 'doc'
      // active docKey 跟随旅程：就绪文档按 stage（需求/计划/构建任务），生成中按 workflow.phase
      // （requirements running→需求文档、project_planning running→项目计划），collecting 阶段无 docKey。
      const stage = applicationLifecycle?.initialization?.stage
      const phase = activeWorkflow?.summary?.phase
      const phaseRunning = activeWorkflow?.summary?.status === 'running'
      const generatingKey =
        phase === 'requirements' && phaseRunning
          ? 'requirement-spec'
          : phase === 'project_planning' && phaseRunning
            ? 'project-plan'
            : phase === 'prepare_build_tasks' && phaseRunning
              ? 'build-task-plan'
              : undefined
      // 生成中优先跟随正在生成的文档(其 tab 此时仍灰),就绪后 stage 接管切到内容。
      const stageActiveKey = designActiveDocKey(stage)
      const targetKey = generatingKey ?? stageActiveKey
      const currentKey = rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
      const shouldOpen = !rightPanel || rightPanel.type !== 'doc'
      const keyChanged = !shouldOpen && currentKey !== targetKey
      if (shouldOpen || keyChanged) {
        setRightPanel({ type: 'doc', docKey: targetKey })
      }
      return
    }
    // 接口目标：右侧固定为「文档」tab（接口详设）。接口无预览/源码，详设是主产物。
    if (activeApiEndpoint) {
      state.type = 'doc'
      if (!rightPanel || rightPanel.type !== 'doc') {
        setRightPanel({ type: 'doc' })
      }
      return
    }
    if (!activePageOption) return
    // 预览只在本次 workflow 构建完成（launch / acceptance，即"集成测试已通过，预览已启动"之后）才出现。
    const previewLaunched =
      activeWorkflow?.summary?.phase === 'launch_project' ||
      activeWorkflow?.summary?.phase === 'acceptance'
    // 预览地址必须是当前页面（origin + 页面路由），不能裸用 preview_url 的 origin（会打开欢迎页）。
    const launchedPreviewUrl = previewTabUrl || activeWorkflow?.result?.preview_url
    if (previewLaunched && launchedPreviewUrl) {
      lastPanelForPageRef.current = activePageId || ''
      // 用户手动切到源码/文档后不强制覆盖回预览（openWorkspaceTab 会置 dismissed）。
      if (!state.dismissed && rightPanel?.type !== 'preview') {
        state.type = 'preview'
        setRightPanel({
          type: 'preview',
          requestKey: `${runtimePreviewBaseUrl}:${activeHeaderTarget.path}`,
          url: launchedPreviewUrl
        })
      }
      return
    }
    // 其余（未设计 / 设计确认 / 构建中）→ 文档；节点进度在对话区 ProcessSteps，右侧不重复展示。
    // 页面切换时强制切到文档面板（未设计页空占位），避免保留上一页/设计阶段的旧内容。
    if (activePageId && activePageId !== lastPanelForPageRef.current) {
      lastPanelForPageRef.current = activePageId
      setRightPanel({ type: 'doc' })
      return
    }
    if (!rightPanel) {
      state.type = 'doc'
      setRightPanel({ type: 'doc' })
    }
  }, [
    rightPanel,
    setRightPanel,
    activePageOption,
    previewTabUrl,
    runtimePreviewBaseUrl,
    activeHeaderTarget.path,
    activePageId,
    activeApiEndpoint,
    isDesignPhase,
    applicationLifecycle?.initialization?.stage,
    activeWorkflow?.summary?.phase,
    activeWorkflow?.summary?.status
  ])

  // #2 右侧工作区 tab：预览 / 源码 / 文档（产出物展示区；交互确认在对话区，无过程 tab）。
  // 文档/源码用页面详细设计（page-designs.json）生成富内容。
  // 仅当页面已设计(大纲 designed / hasDetailPlan)才读设计数据——待设计页面无设计 → 源码/文档显示引导。
  // 页面设计已生成（确认卡出现）：detail_review 产物在确认前即可在右侧文档区查看/编辑，
  // 与设计阶段「需求文档生成后右侧显示、对话只授权」一致。
  const workflowForDisplay = latestWorkflowForDisplay as WorkflowRunPayload | undefined
  const pageDesignGenerated = Boolean(
    activePageOption &&
      workflowForDisplay &&
      String(workflowForDisplay.summary?.phase) === 'detail_confirmation' &&
      String(workflowForDisplay.summary?.status) === 'requires_user_input'
  )
  // 记忆「该页面已生成过详细设计」：detail_confirmation 到过确认/完成后，后续构建/预览阶段
  // 右侧文档保持页面需求文档不变（不因 designed 未刷新而清空），等预览就绪再切预览。
  const pageDesignEverGeneratedRef = useRef<Set<string>>(new Set())
  if (activePageOption && (pageDesignGenerated || activePageOption.designed || activePageOption.hasDetailPlan)) {
    pageDesignEverGeneratedRef.current.add(activePageOption.pageId)
  }
  const activePageDesign =
    activePageOption &&
    (activePageOption.designed ||
      activePageOption.hasDetailPlan ||
      pageDesignGenerated ||
      pageDesignEverGeneratedRef.current.has(activePageOption.pageId))
      ? (scenario.pageDesigns as Record<string, PageDesign | undefined>)[activePageOption.pageId] ||
        (scenario.designedPageDesigns as Record<string, PageDesign | undefined>)[activePageOption.pageId]
      : undefined
  // 设计阶段：右侧工作区 tab 就是应用级三份产物文档（需求文档 / 项目计划 / 构建任务），
  // 按旅程进度（生命周期 stage）渐进可用——新应用刚进入（澄清中）三份都未生成，tab 置灰；
  // 需求确认后「需求文档」可用，规划确认后「项目计划」可用，进入开发前「构建任务」可用。
  // 数据直接来自 mock 产物（requirement-spec / project-plan / build-task-plan），与规划会话共用构建器。
  const designStage = applicationLifecycle?.initialization?.stage
  const designDocs = isDesignPhase
    ? ([
        {
          key: 'requirement-spec' as WorkspaceDocKey,
          title: '需求文档',
          path: 'specs/requirement.md',
          content: buildRequirementSpecDoc(scenario.requirementSpec, application.name)
        },
        {
          key: 'project-plan' as WorkspaceDocKey,
          title: '项目计划',
          path: 'plans/project-plan.md',
          content: buildProjectPlanDoc(scenario.projectPlan, application.name)
        },
        {
          key: 'build-task-plan' as WorkspaceDocKey,
          title: '构建任务',
          path: 'plans/build-task-plan.json',
          content: buildBuildTaskPlanDoc(scenario.buildTaskPlan)
        }
      ] as Array<{ key: WorkspaceDocKey; title: string; path: string; content: string }>).map((doc) => ({
        ...doc,
        available: designStageReached(designStage, DESIGN_DOC_THRESHOLDS[doc.key])
      }))
    : undefined
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
  const activeDesignDoc = designDocs?.find((doc) => doc.key === activeDesignDocKey)
  const designDocName = activeDesignDoc?.title
  // 开发阶段：接口详设文档（endpoint-designs.json）；无设计数据时回落 app 级兜底。
  const activeEndpointDesign = activeApiEndpoint
    ? (scenario.endpointDesigns as Record<string, Record<string, unknown> | undefined>)[
        activeApiEndpoint.endpointId
      ]
    : undefined
  const activeEndpointDocTitle = activeEndpointDesign
    ? `${String(activeEndpointDesign.method || 'GET').toUpperCase()} ${activeEndpointDesign.path || ''}`
    : undefined
  // 文档头部地址栏：设计阶段显示当前产物路径，开发阶段接口显示 method+path。
  const docTitle = isDesignPhase ? activeDesignDoc?.path : activeEndpointDocTitle
  // 设计阶段文档生成中：workflow 正在 running 对应文档（需求/项目计划），右栏走生成中占位。
  // 生成完成 stage 推进后该文档就绪（content 立即从静态产物取），docKey 切过去，generating 结束。
  const designPhase = activeWorkflow?.summary?.phase
  const designPhaseRunning = activeWorkflow?.summary?.status === 'running'
  const docGenerating =
    isDesignPhase &&
    designPhaseRunning &&
    (designPhase === 'requirements' ||
      designPhase === 'project_planning' ||
      designPhase === 'prepare_build_tasks')
  // 开发阶段：详细设计生成中（detail_confirmation running，尚未到确认卡），右侧文档区富加载占位。
  const devDetailGenerating =
    !isDesignPhase &&
    activePageOption &&
    workflowForDisplay?.summary?.phase === 'detail_confirmation' &&
    String(workflowForDisplay?.summary?.status) === 'running'
  const docContent = isDesignPhase
    ? (activeDesignDocKey ? editedDesignDocs[activeDesignDocKey] : undefined) ?? activeDesignDoc?.content
    : activePageOption
      ? activePageDesign
        ? buildPageDesignDoc(activePageDesign)
        // 待设计页面：右侧文档区空占位（DocPanel 引导），详细设计生成后才有文档。
        : ''
      : activeEndpointDesign
        ? buildEndpointDesignDoc(activeEndpointDesign)
        : ''
  // 源码仅在页面已设计(确认后)时生成：设计生成后右侧先显示设计文档，源码随节点推进到确认才可用。
  const activeSource =
    activePageOption &&
    (activePageOption.designed || activePageOption.hasDetailPlan) &&
    activePageDesign
      ? buildPageSource(activePageDesign, activePageOption.pageId || 'page')
      : undefined
  // 预览仅在工作流构建完成(launch/acceptance，"集成测试已通过，预览已启动")后可用；
  // 待设计/设计中阶段预览灰显，右侧内容随会话节点推进。
  const devPreviewLaunched =
    activeWorkflow?.summary?.phase === 'launch_project' ||
    activeWorkflow?.summary?.phase === 'acceptance'
  const workspaceTabs: WorkspaceTab[] = isDesignPhase
    ? (designDocs || []).map((doc) => ({ key: doc.key, label: doc.title, available: doc.available }))
    : [
        { key: 'preview', label: '预览', available: devPreviewLaunched && Boolean(previewTabUrl) },
        { key: 'source', label: '源码', available: Boolean(activeSource) },
        { key: 'doc', label: '文档', available: true }
      ]
  // 当前激活的工作区 tab：设计阶段 = 当前产物 docKey，其余按 rightPanel 类型。
  const activeWorkspaceTab: WorkspaceTabKey =
    rightPanel?.type === 'doc'
      ? ((rightPanel.docKey || 'doc') as WorkspaceTabKey)
      : ((rightPanel?.type || 'doc') as WorkspaceTabKey)
  const openWorkspaceTab = (key: WorkspaceTabKey): void => {
    // 手动切换 tab 后不再自动升级/重开（含预览强制覆盖回切）。
    autoOpenStateRef.current.type = null
    autoOpenStateRef.current.dismissed = true
    if (key === 'preview' && previewTabUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${runtimePreviewBaseUrl}:${activeHeaderTarget.path}`,
        url: previewTabUrl
      })
    } else if (key === 'doc') {
      setRightPanel({ type: 'doc' })
    } else if (key === 'source') {
      setRightPanel({ type: 'source' })
    } else if (WORKSPACE_DOC_KEYS.has(key)) {
      setRightPanel({ type: 'doc', docKey: key as WorkspaceDocKey })
    }
  }
  const activeWorkflowPhase = String(
    activeWorkflow?.summary?.phase ||
      activeWorkflow?.result?.phase ||
      activeWorkflow?.state?.phase ||
      ''
  )
  const activeSessionTargetKey = sessionDetailTargetKey(activeSession)
  const activeWorkflowTargetKey = workflowDetailTargetKey(latestWorkflowForDisplay)
  const activeWorkflowMatchesTarget = Boolean(
    activeTargetKey &&
      (activeWorkflowTargetKey
        ? activeWorkflowTargetKey === activeTargetKey
        : activeSessionTargetKey
          ? activeSessionTargetKey === activeTargetKey
          : interactingDetailTargetKey === activeTargetKey)
  )
  const detailConfirmationWaitingReview =
    !loading &&
    activeWorkflowMatchesTarget &&
    (activeWorkflowPhase === 'detail_confirmation' ||
      workflowHasDetailReview(latestWorkflowForDisplay))
  // 待设计目标（页面/接口）作为对话节点提示：进入开发未操作时，对话区末尾出现「尚未详细设计」节点。
  const lockedDetailTarget =
    (requiresPageDetailDesign(activePageOption) ||
      requiresEndpointDetailDesign(activeApiEndpointOption?.endpoint)) &&
    displayedPlanExecutionMode === 'idle' &&
    !detailConfirmationWaitingReview
      ? activePageOption
        ? { type: 'page' as const, label: activePageOption.label, path: activePageOption.path, purpose: activePageOption.purpose }
        : activeApiEndpoint
          ? {
              type: 'endpoint' as const,
              label: activeApiEndpoint.label,
              path: activeApiEndpointOption?.endpoint.path,
              purpose: activeApiEndpointOption?.endpoint.summary
            }
          : undefined
      : undefined
  // 对话节点承载的待设计目标（含模板选择），传给 MessageList 的 DetailConfirmationPageSelector。
  const lockedPage = lockedDetailTarget?.type === 'page' ? activePageOption : undefined
  const lockedEndpoint = lockedDetailTarget?.type === 'endpoint'
    ? {
        apiContractId: activeApiEndpoint?.apiContractId || '',
        endpointId: activeApiEndpoint?.endpointId || '',
        hasDetailPlan: Boolean(
          activeApiEndpointOption?.endpoint.designed ||
            activeApiEndpointOption?.endpoint.hasDetailPlan
        ),
        label: activeApiEndpoint?.label || '',
        path: activeApiEndpointOption?.endpoint.path,
        purpose: activeApiEndpointOption?.endpoint.summary
      }
    : undefined
  const detailProgressVisible =
    loading &&
    activeWorkflowMatchesTarget &&
    (generatingDetailTargetKey === activeTargetKey ||
      activeWorkflowPhase === 'detail_confirmation') &&
    developmentPlanningReady &&
    Boolean(activeApiEndpoint || activePageOption) &&
    !detailConfirmationWaitingReview
  const initialDetailDesignSelectionRequired = requiresInitialDetailDesignSelection(hasPageDesigns)
  const hasActiveDetailWorkflow =
    interactingDetailTargetKey === activeTargetKey &&
    Boolean(activeApiEndpoint || activePageOption || activeSession || latestWorkflowForDisplay)
  const detailTargetSelectionRequired =
    developmentPlanningReady &&
    initialDetailDesignSelectionRequired &&
    !hasActiveDetailWorkflow &&
    !detailProgressVisible &&
    !detailConfirmationWaitingReview
  const activeSessionUpdatedAt = sessions.find(
    (session) => session.id === activeSessionId
  )?.updatedAt

  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    if (activeApiEndpoint) return
    if (detailTargetSelectionRequired) return
    setActiveDetailTarget((currentTarget) => {
      if (currentTarget.type === 'endpoint') return currentTarget
      if (currentTarget.type === 'none') return currentTarget
      const currentPageId = currentTarget.pageId
      if (developmentPlanningPages.length === 0) return currentTarget
      if (developmentPlanningPages.some((page) => page.pageId === currentPageId)) {
        return currentTarget
      }
      const fallbackPageId =
        developmentPlanningPages.find((page) => page.designed)?.pageId ||
        developmentPlanningPages[0]?.pageId ||
        ''
      return fallbackPageId ? { type: 'page', pageId: fallbackPageId } : { type: 'none' }
    })
  }, [activeApiEndpoint, developmentPlanningPages, detailTargetSelectionRequired])

  // 打开历史页面或接口会话时同步目标上下文，避免标题与消息归属不一致。
  useEffect(() => {
    const session = sessions.find((item) => item.id === activeSessionId)
    if (!session) return
    if (session?.apiContractId && session.endpointId) {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: session.apiContractId,
        endpointId: session.endpointId,
        endpointKey: `${session.apiContractId}:${session.endpointId}`,
        label: session.endpointLabel || session.title
      })
      return
    }
    const sessionPageId = session?.pageId
    if (!sessionPageId) {
      setActiveDetailTarget({ type: 'none' })
      return
    }
    const resolvedPageId = resolvePlanningPageId(developmentPlanningPages, sessionPageId)
    if (resolvedPageId) {
      setActiveDetailTarget({ type: 'page', pageId: resolvedPageId })
    }
  }, [activeSessionId, developmentPlanningPages, sessions])

  /** 使用当前前端端口和所选页面路由打开独立全屏预览窗口。 */
  const handleOpenFullscreenPreview = async (): Promise<void> => {
    setPreviewError('')

    try {
      const targetUrl = composePreviewUrl(runtimePreviewBaseUrl, activeHeaderTarget.path)
      if (!targetUrl) {
        throw new Error(runtimePreviewLaunchError || '前端服务尚未启动完成，暂时无法预览页面')
      }
      await openPreviewWindow(targetUrl)
    } catch (caughtError) {
      setPreviewError(caughtError instanceof Error ? caughtError.message : '无法打开网页预览')
    }
  }

  const handleOpenCodeChangeFile = (
    codeChanges: WorkspaceCodeChangeSet,
    selectedPath: string
  ): void => {
    setRightPanel({ type: 'diff', codeChanges, selectedPath })
  }

  const handleShowSkills = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('skills')
  }

  const handleShowFiles = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('files')
  }

  const handleShowSettings = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('settings')
  }

  /** 新建普通对话时退出页面/API 目标上下文，避免后续消息被旧目标接管。 */
  const handleCreateChatSession = (): void => {
    setActiveView('chat')
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleCreateSessionFromList()
  }

  /** 在指定页面下新建独立会话，并立即切换到该页面。 */
  const handleCreatePageSession = async (pageId: string, pageLabel: string): Promise<void> => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey(pageDetailTargetKey(pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId })
    await createPageSession(pageId, pageLabel)
  }

  /** 从应用大纲切换页面；没有消息历史时仅展示空白上下文，不提前创建会话。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey(pageDetailTargetKey(page.pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId: page.pageId })
    handleSelectPage(page.pageId).catch(() => undefined)
  }

  // 进入开发阶段且未选中任何目标时，自动落到第一个待设计页面（空白任务起点），
  // 对话区下方出现 locked「开始详细设计」卡。每次进入开发阶段只触发一次。
  const autoSelectDevPageRef = useRef(false)
  if (activeWorkbenchPhase !== 'development') autoSelectDevPageRef.current = false
  useEffect(() => {
    if (isDesignPhase || activeWorkbenchPhase !== 'development') return
    if (autoSelectDevPageRef.current) return
    if (activeApiEndpoint) return
    if (activeDetailTarget.type !== 'none') return
    if (developmentPlanningPages.length === 0) return
    const firstUndesigned =
      developmentPlanningPages.find((page) => !page.designed && !page.hasDetailPlan) ||
      developmentPlanningPages[0]
    if (!firstUndesigned) return
    autoSelectDevPageRef.current = true
    setPreviewError('')
    setActiveView('chat')
    setInteractingDetailTargetKey(pageDetailTargetKey(firstUndesigned.pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId: firstUndesigned.pageId })
    // 不再调用 handleSelectPage：其内部 onCloseRightPanel 会清空右侧面板，与 autoOpen 的
    // 强制刷新产生竞态（进开发时面板时开时关）。页面 session 由挡板注入 effect 的
    // ensurePageSession 创建，右侧面板由 autoOpenStateRef 按页面切换强制切到文档占位。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkbenchPhase, isDesignPhase, activeDetailTarget.type, activeApiEndpoint, developmentPlanningPages])

  // 锁定目标作为对话历史消息注入（含模板选择交互）：进开发选中待设计页面时，
  // 向该页面 session 追加一条 detailBlocker assistant 消息，点「开始详细设计」后该消息
  // 保留在历史里，与后续 user/assistant 节点串成完整对话链，回看可见。
  const blockerInjectedRef = useRef('')
  useEffect(() => {
    if (isDesignPhase || activeWorkbenchPhase !== 'development') return
    if (displayedPlanExecutionMode !== 'idle') return
    const target = lockedDetailTarget
    if (!target || target.type !== 'page' || !activePageOption) return
    const pageId = activePageOption.pageId
    if (!pageId || blockerInjectedRef.current === pageId) return
    blockerInjectedRef.current = pageId
    ensurePageSession(pageId, target.label)
      .then((identity) => {
        // 挡板注入创建页面 session 时 createNewSession 会 onCloseRightPanel 关闭面板，
        // 与 autoOpen 的恢复存在竞态（覆盖后 rightPanel 无变化不再触发）。此处强制恢复文档占位，
        // 保证切换到待设计页面时右侧面板常驻打开。
        setRightPanel({ type: 'doc' })
        const existing = getSessionMessages(identity.key)
        if (existing.some((message) => message.detailBlocker)) return
        const blockerMessage: AgentChatMessage = {
          id: Date.now(),
          role: 'assistant',
          content: '',
          detailBlocker: {
            pageId,
            label: target.label,
            path: target.path,
            purpose: target.purpose
          },
          createdAt: Date.now()
        }
        setSessionMessages(identity.key, [...existing, blockerMessage])
      })
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkbenchPhase, isDesignPhase, displayedPlanExecutionMode, lockedDetailTarget, activePageOption])

  // 阶段切换会话隔离：切到设计阶段恢复产品 Agent 的设计会话（无 pageId/endpointId），
  // 切回开发阶段恢复当前页面会话。避免切阶段后对话内容仍是上一阶段、只换了 Agent 名。
  const phaseSwitchHandledRef = useRef<WorkbenchPhase | ''>('')
  useEffect(() => {
    if (phaseSwitchHandledRef.current === activeWorkbenchPhase) return
    phaseSwitchHandledRef.current = activeWorkbenchPhase
    if (isDesignPhase) {
      // 设计阶段：激活无 pageId 的设计会话（产品 Agent 的需求/计划确认链）。
      // 取最近一条无页面/接口归属的会话；设计阶段通常只有一条设计会话。
      const designSession = sessions
        .filter((session) => !session.pageId && !session.endpointId)
        .sort((a, b) => b.updatedAt - a.updatedAt)[0]
      if (designSession) {
        handleOpenChatSession(designSession.id).catch(() => undefined)
      }
    } else if (activeWorkbenchPhase === 'development') {
      // 开发阶段：若当前停在设计会话，切到首个页面会话（或保持已选页面）。
      const active = sessions.find((session) => session.id === activeSessionId)
      if (active && !active.pageId && !active.endpointId) {
        const pageSession = sessions.find((session) => session.pageId && session.messageCount > 0)
        if (pageSession) handleOpenChatSession(pageSession.id).catch(() => undefined)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkbenchPhase, isDesignPhase])

  /** 授权条「放弃」:丢弃该文档编辑版,回到 Agent 生成版。 */
  const handleDiscardArtifact = (docKey: WorkspaceDocKey): void => {
    setEditedDesignDocs((prev) => {
      if (!(docKey in prev)) return prev
      const next = { ...prev }
      delete next[docKey]
      return next
    })
  }

  /** 从应用大纲切换 API；页面和 API 目标互斥，因此会清空当前页面选中态。 */
  const handleApiEndpointSelect = (target: ActiveApiEndpointTarget): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey(endpointDetailTargetKey(target.apiContractId, target.endpointId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ ...target, type: 'endpoint' })
    handleSelectEndpoint(target.apiContractId, target.endpointId).catch(() => undefined)
  }

  /** 为当前 API endpoint 新建一条独立会话历史。 */
  const handleCreateEndpointSession = async (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ): Promise<void> => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey(endpointDetailTargetKey(apiContractId, endpointId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({
      type: 'endpoint',
      apiContractId,
      endpointId,
      endpointKey: `${apiContractId}:${endpointId}`,
      label: endpointLabel
    })
    await createEndpointSession(apiContractId, endpointId, endpointLabel)
  }

  /** 启动当前页面的详细设计；解锁状态仍以后续持久化目录检查为准。 */
  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    },
  ): Promise<void> => {
    const targetKey = pageDetailTargetKey(pageId)
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    setActiveDetailTarget({ type: 'page', pageId })
    const started = await handleStartDetailConfirmation(pageId, pageLabel, hasDetailPlan, templateParams)
    if (started) {
      onPlanningArtifactsRefresh()
    } else {
      setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
    }
  }

  /** 对话节点「开始详细设计」：按当前待设计目标（含可选模板）启动页面/接口设计。 */
  const handleStartDetailDesign = async (
    targetType: 'page' | 'endpoint',
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string
      endpointId?: string
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<void> => {
    if (targetType === 'endpoint') {
      await handleStartEndpointDesign(targetId, targetLabel, hasDetailPlan, targetContext)
      return
    }
    await handleStartPageDesign(
      targetId,
      targetLabel,
      hasDetailPlan,
      targetContext && (targetContext.templateId || targetContext.templateSourcePath)
        ? {
            templateId: targetContext.templateId,
            templateName: targetContext.templateName,
            templateSourcePath: targetContext.templateSourcePath,
          }
        : undefined,
    )
  }

  /** 启动当前接口的详细设计；解锁状态仍以后续持久化目录检查为准。 */
  const handleStartEndpointDesign = async (
    endpointTargetId: string,
    endpointLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string
      endpointId?: string
    }
  ): Promise<void> => {
    const targetKey = targetContext?.apiContractId
      ? endpointDetailTargetKey(
          targetContext.apiContractId,
          targetContext.endpointId || endpointTargetId
        )
      : ''
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    if (targetContext?.apiContractId) {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: targetContext.apiContractId,
        endpointId: targetContext.endpointId || endpointTargetId,
        endpointKey: `${targetContext.apiContractId}:${targetContext.endpointId || endpointTargetId}`,
        label: endpointLabel
      })
    } else {
      setActiveDetailTarget({ type: 'none' })
    }
    const started = await handleStartEndpointDetailConfirmation({
      apiContractId: targetContext?.apiContractId,
      endpointId: targetContext?.endpointId || endpointTargetId,
      endpointLabel,
      hasDetailPlan
    })
    if (started) {
      onPlanningArtifactsRefresh()
    } else {
      setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
    }
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    const session = sessions.find((item) => item.id === sessionId)
    setInteractingDetailTargetKey(sessionDetailTargetKey(session))
    setGeneratingDetailTargetKey('')
    if (session?.apiContractId && session.endpointId) {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: session.apiContractId,
        endpointId: session.endpointId,
        endpointKey: `${session.apiContractId}:${session.endpointId}`,
        label: session.endpointLabel || session.title
      })
    } else if (session?.pageId) {
      setActiveDetailTarget({ type: 'page', pageId: session.pageId })
    } else {
      setActiveDetailTarget({ type: 'none' })
    }
    await handleOpenSession(sessionId)
  }

  /** 提交详细设计确认后进入 DAG/构建链路，停止使用详细设计生成进度遮罩。 */
  const handleSubmitWorkflowClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<void> => {
    setGeneratingDetailTargetKey('')
    const submitted = await handleSubmitClarification(workflow, answers)
    // 确认后刷新规划产物：开发阶段确认详细设计(detail_review)后 markPageDesigned，
    // 大纲与右侧按新状态展示（待设计 → 已设计）。
    // markPageDesigned 在 mock 确认处理中异步执行，立即刷新读不到新状态，延迟二次刷新兜底。
    if (submitted) {
      onPlanningArtifactsRefresh()
      // mock 确认处理（markPageDesigned）在提交后异步执行，延迟二次刷新确保大纲/产物读到已设计。
      window.setTimeout(onPlanningArtifactsRefresh, 3000)
    }
  }

  /** 滚动到现有 Workflow 进度区域，不改变消息列表和中央内容结构。 */
  const handleViewPlan = (): void => {
    document.querySelector(`.${CLASS_PREFIX}-process-steps`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center'
    })
  }

  /** 把底部结构化确认转换为当前 Workflow 已支持的确认答案。 */
  const handleConfirmPlanInteraction = (decision: 'reject' | 'once' | 'always'): void => {
    if (!activeWorkflow || !scopedExecution?.pendingInteraction) return
    const interactionType = scopedExecution.pendingInteraction.type
    const answerKey =
      interactionType === 'repair_scope_confirmation'
        ? 'repair_scope_confirmation'
        : 'agent_approval'
    const answer = planInteractionAnswer(interactionType, decision)
    void handleSubmitClarification(activeWorkflow, { [answerKey]: answer })
  }

  /** 用户关闭技能后立即清理当前会话草稿中的同名标签。 */
  const handleSkillDisabled = (skillName: string): void => {
    const nextSkills = selectedSkills.filter((skill) => skill.name !== skillName)
    if (nextSkills.length !== selectedSkills.length) {
      setSelectedSkillsByKey(draftKey, nextSkills)
    }
  }

  const showRightPanel = rightPanelOpen && Boolean(rightPanel)
  return (
    <section
      className={cx(
        'ai-chat-panel',
        showRightPanel && 'embedded-preview-open',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        elementInspectionActive && 'element-inspection-active',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <SessionSidebar
        activeSessionId={activeSessionId}
        deletingSessionId={deletingSessionId}
        forceCollapsed={isDesignPhase}
        loadingSessions={loadingSessions}
        outlineLocked={detailTargetSelectionRequired}
        onCreateSession={handleCreateChatSession}
        onCreatePageSession={handleCreatePageSession}
        onCreateEndpointSession={handleCreateEndpointSession}
        onDeleteSession={handleDeleteSession}
        onApiEndpointSelect={handleApiEndpointSelect}
        onOpenSession={handleOpenChatSession}
        onPageSelect={handlePageSelect}
        onShowFiles={handleShowFiles}
        onShowSettings={handleShowSettings}
        onShowSkills={handleShowSkills}
        pages={developmentPlanningPages}
        pageTree={developmentPlanningPageTree}
        apiContracts={developmentPlanningApiContracts}
        selectedApiEndpointKey={activeApiEndpoint?.endpointKey || ''}
        selectedPageId={activePageId}
        filesActive={activeView === 'files'}
        sessionError={sessionError}
        sessionRunStates={sessionRunStates}
        sessions={sessions}
        settingsActive={activeView === 'settings'}
        skillsActive={activeView === 'skills'}
      />

      <div className={cx('ai-chat-assistant')}>
        {activeView === 'skills' ? (
          <SkillsPage onSkillDisabled={handleSkillDisabled} theme={theme} />
        ) : activeView === 'files' ? (
          <AgentFilesPage />
        ) : activeView === 'settings' ? (
          <SettingsPage application={application} onSaved={onApplicationUpdate} />
        ) : (
          <div className={cx('ai-chat-main')}>
            <PageContextHeader
              description={activeHeaderTarget.description}
              keyFeatures={activeHeaderTarget.keyFeatures}
              lastAnalyzedAt={activeSessionUpdatedAt}
              pagePath={activeHeaderTarget.path}
              pageTitle={activeHeaderTarget.title}
              status={activeHeaderStatus}
              targetType={activeHeaderTarget.type}
              theme={theme}
            />

            {previewError && (
              <Alert
                className={cx('preview-action-error')}
                message={previewError}
                showIcon
                type="error"
              />
            )}

            <MessageList
              applicationLifecycle={applicationLifecycle}
              codeChangeActionsDisabled={loading || workspaceBusy}
              copy={copy}
              key={activeSession?.key || draftKey}
              loading={loading}
              lockedEndpoint={lockedEndpoint}
              lockedPage={lockedPage}
              messages={messages}
              onDiscardArtifact={handleDiscardArtifact}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onRevertCodeChanges={requestCodeChangeRevert}
              onSubmitClarification={handleSubmitWorkflowClarification}
              onStartDetailDesign={handleStartDetailDesign}
              revertingCodeChangeIds={revertingCodeChangeIds}
            />

            {shouldRenderPlanExecutionDock(
              displayedPlanExecutionMode,
              directModificationRunning
            ) ? (
              <>
                {planExecutionShowsDebugResume(displayedPlanExecutionMode) &&
                  !targetExecutionContext.dependencyLocked && (
                    <ChatComposer
                      activeWorkflow={activeWorkflow}
                      copy={copy}
                      debugOnly
                      draft=""
                      error={error}
                      initialResumeFrom={workflowResumeNode(activeWorkflow, scopedExecution?.phase)}
                      key={`paused-debug-${activeWorkflow?.runId || ''}-${scopedExecution?.phase || ''}`}
                      loading={loading}
                      onDraftChange={() => undefined}
                      onSelectedSkillsChange={() => undefined}
                      onSend={handleResumePlan}
                      onStopGenerating={handleStopGenerating}
                      stopping={stopping}
                      selectedSkills={[]}
                      workspaceBusy={workspaceBusy}
                      workspaceRoot={workspaceRoot}
                    />
                  )}
                <PlanExecutionDock
                  dependencyLocked={targetExecutionContext.dependencyLocked}
                  error={scopedExecution?.error?.message || error}
                  execution={scopedExecution}
                  mode={displayedPlanExecutionMode}
                  onAccept={handleAcceptPreview}
                  onAdjust={(feedback) => void handleAdjustPlan(feedback)}
                  onConfirmInteraction={handleConfirmPlanInteraction}
                  onEnd={() => void handleEndPlan(scopedExecution?.runId)}
                  onOpenPreview={() => void handleOpenFullscreenPreview()}
                  onRetry={() => void handleRetryPlan()}
                  onStop={
                    loading
                      ? handleStopGenerating
                      : () => void handleStopPlan(scopedExecution?.runId)
                  }
                  onViewPlan={handleViewPlan}
                />
              </>
            ) : (
              <ChatComposer
                activeWorkflow={activeWorkflow}
                copy={copy}
                draft={draft}
                error={error}
                loading={loading}
                onDraftChange={(value) => setDraftByKey(draftKey, value)}
                onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                onSend={handleSend}
                onStopGenerating={handleStopGenerating}
                stopping={stopping}
                selectedSkills={selectedSkills}
                workspaceBusy={workspaceBusy}
                workspaceRoot={workspaceRoot}
              />
            )}
          </div>
        )}
        {elementInspectionActive && (
          <div aria-hidden="true" className={cx('element-inspection-interaction-mask')} />
        )}
      </div>

      {showRightPanel && (
        <div
          aria-label="拖动调整右侧面板宽度"
          aria-orientation="vertical"
          aria-valuenow={assistantPanelWidth}
          className={cx('panel-split-handle', splitDragging && 'dragging')}
          aria-disabled={elementInspectionActive}
          onKeyDown={elementInspectionActive ? undefined : handlePanelSplitKeyDown}
          onMouseDown={elementInspectionActive ? undefined : handlePanelSplitDragStart}
          role="separator"
          tabIndex={elementInspectionActive ? -1 : 0}
          title="拖动调整左右面板宽度"
        >
          <HolderOutlined className={cx('panel-split-handle-icon')} />
        </div>
      )}

      {showRightPanel &&
        (rightPanel?.type === 'preview' ||
          rightPanel?.type === 'doc' ||
          rightPanel?.type === 'process' ||
          rightPanel?.type === 'source') && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={workspaceTabs}
            active={activeWorkspaceTab}
            onChange={openWorkspaceTab}
            onClose={() => {
              autoOpenStateRef.current.dismissed = true
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            {rightPanel.type === 'preview' && (
              <BrowserPreviewPanel
                application={application}
                pages={developmentPlanningPages}
                requestKey={rightPanel.requestKey}
                requestedUrl={rightPanel.url}
                previewBaseUrl={runtimePreviewBaseUrl}
                selectedPagePath={activeHeaderTarget.type === 'page' ? activeHeaderTarget.path : '/'}
                errorMessage={runtimePreviewLaunchError}
                onInspectingChange={setElementInspectionActive}
              />
            )}
            {rightPanel.type === 'doc' && (
              <DocPanel
                content={docContent}
                docName={designDocName}
                generating={docGenerating || devDetailGenerating}
                onSaveEdit={(draft) => {
                  if (activeDesignDocKey) {
                    setEditedDesignDocs((prev) => ({ ...prev, [activeDesignDocKey]: draft }))
                  }
                }}
                title={docTitle}
              />
            )}
            {rightPanel.type === 'source' && (
              <SourcePanel
                filePath={activeSource?.filePath || ''}
                content={activeSource?.content || ''}
              />
            )}
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'diff' && (
        <div className={cx('embedded-preview-pane', 'diff-detail-pane')}>
          <CodeDiffDetailPanel
            codeChanges={rightPanel.codeChanges}
            selectedPath={rightPanel.selectedPath}
            onClose={() => setRightPanel(undefined)}
          />
        </div>
      )}
    </section>
  )
}

/** 把授权或修复范围选择转换为 Workflow 可恢复的结构化答案文本。 */
function planInteractionAnswer(
  interactionType: string,
  decision: 'reject' | 'once' | 'always'
): string {
  if (interactionType === 'repair_scope_confirmation') {
    return decision === 'reject' ? '拒绝修复范围' : '批准修复范围'
  }
  if (decision === 'always') return '始终允许'
  if (decision === 'once') return '仅本次允许'
  return '拒绝执行'
}

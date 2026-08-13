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
  ApplicationMenuItem,
  EditorMode,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import { composePreviewUrl, cx, previewOrigin } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import MessageList from './components/MessageList'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import DocPanel from './components/DocPanel'
import SourcePanel from './components/SourcePanel'
import {
  buildEndpointDesignDoc,
  buildEndpointSource,
  buildPageDesignDoc,
  buildPageSource,
  buildProjectPlanDoc,
  buildRequirementSpecDoc,
  buildReviewReport,
  type PageDesign
} from '../../workbenchArtifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { isEndpointDesigned, isPageDesigned } from '../../mock/designState'
import { isInitialPlanningPhase } from '../../workbenchPhase'
import type { WorkbenchPhase } from '../../workbenchPhase'
import {
  artifactIdsForSession,
  documentArtifactId,
  endpointArtifactId,
  pageArtifactId,
  resolveArtifactAccess,
  resolveArtifactOwners,
  type WorkbenchArtifact,
  type WorkbenchArtifactAccess
} from '../../workbenchDomain'
import RightPanelTabs, {
  type WorkspaceTab,
  type WorkspaceTabKey
} from './components/RightPanelTabs'
import SessionSidebar from './components/SessionSidebar'
import PageContextHeader, { type ConversationArtifact } from './components/PageContextHeader'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import type { RelatedEndpointContext } from './hooks/useChatSessions'
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
  planExecutionContextForEndpoint,
  planExecutionContextForPage
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
  applicationPreviewMode: boolean
  onApplicationPreviewModeChange: (open: boolean) => void
  theme: 'light' | 'dark'
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
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

/** 为应用预览地址附加当前版本标识，让独立预览服务渲染对应版本快照。 */
function composeVersionPreviewUrl(baseUrl: string, path: string, versionKey: string): string {
  const previewUrl = composePreviewUrl(baseUrl, path)
  if (!previewUrl || !versionKey) return previewUrl
  const parsedUrl = new URL(previewUrl)
  parsedUrl.searchParams.set('version', versionKey)
  return parsedUrl.toString()
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
  'generating_application_template_files',
  'ready_for_workbench'
]
function designStageReached(stage: string | undefined, threshold: string): boolean {
  if (!stage) return false
  const current = DESIGN_DOC_STAGE_ORDER.indexOf(stage)
  const target = DESIGN_DOC_STAGE_ORDER.indexOf(threshold)
  return current >= 0 && target >= 0 && current >= target
}

/** 项目计划只有在用户确认且保存完成后，才算正式解锁开发产物目录。 */
function isDevelopmentCatalogConfirmed(stage: string | undefined): boolean {
  return stage === 'ready_for_workbench'
}

/** 设计阶段两份正式产物文档的 key → 可用的门槛 stage。 */
const DESIGN_DOC_THRESHOLDS: Record<WorkspaceDocKey, string> = {
  'requirement-spec': 'generating_requirement_spec',
  'project-plan': 'generating_project_plan'
}

/** 当前 stage 对应"最新就绪"的文档 key：生成完成(stage 推进)自动跟随到对应文档。 */
function designActiveDocKey(stage: string | undefined): WorkspaceDocKey | undefined {
  if (designStageReached(stage, 'generating_project_plan')) return 'project-plan'
  if (designStageReached(stage, 'generating_requirement_spec')) return 'requirement-spec'
  return undefined
}

/** 将设计文档生成与确认进度转换为产物树的三态状态。 */
function designArtifactStatus(
  stage: string | undefined,
  key: WorkspaceDocKey
): 'not-started' | 'in-progress' | 'completed' {
  if (key === 'requirement-spec') {
    if (designStageReached(stage, 'generating_project_plan')) return 'completed'
    if (designStageReached(stage, 'generating_requirement_spec')) return 'in-progress'
    return 'not-started'
  }
  if (designStageReached(stage, 'ready_for_workbench')) return 'completed'
  if (designStageReached(stage, 'generating_project_plan')) return 'in-progress'
  return 'not-started'
}

/** 按未开始、进行中、已完成的单向顺序合并状态，禁止工作流回放造成视觉倒退。 */
function advanceArtifactStatus(
  current: 'not-started' | 'in-progress' | 'completed',
  observed: 'not-started' | 'in-progress' | 'completed'
): 'not-started' | 'in-progress' | 'completed' {
  const order = { 'not-started': 0, 'in-progress': 1, completed: 2 }
  return order[observed] > order[current] ? observed : current
}

/** 将同一开发对话负责的页面与接口设计合并为一份正式详细设计文档。 */
function buildConversationDesignDoc(input: {
  title: string
  pageDesign?: PageDesign
  endpointDesign?: Record<string, unknown>
}): string {
  const sections: string[] = [`# ${input.title} · 详细设计`]
  if (input.pageDesign) {
    sections.push('## 页面设计', buildPageDesignDoc(input.pageDesign).replace(/^#\s+.+\n+/, ''))
  }
  if (input.endpointDesign) {
    sections.push(
      '## 接口设计',
      buildEndpointDesignDoc(input.endpointDesign).replace(/^#\s+.+\n+/, '')
    )
  }
  return sections.length > 1 ? sections.join('\n\n') : ''
}

/**
 * 设计阶段右栏文档 tab 跟随规则：生成中按 workflow.phase 跟随正在生成的文档，
 * 就绪后按 lifecycle.stage 接管切到内容。collecting 阶段无 docKey。
 */
function resolveDesignDocKey(
  stage: string | undefined,
  phase: string | undefined,
  phaseRunning: boolean
): WorkspaceDocKey | undefined {
  const generatingKey =
    phase === 'requirements' && phaseRunning
      ? 'requirement-spec'
      : phase === 'project_planning' && phaseRunning
        ? 'project-plan'
        : undefined
  return generatingKey ?? designActiveDocKey(stage)
}

const WORKSPACE_DOC_KEYS: ReadonlySet<string> = new Set(['requirement-spec', 'project-plan'])

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

/** 从页面设计的接口依赖与响应绑定中解析同一实现任务负责的 endpoint。 */
function resolvePageRelatedEndpoint(
  pageId: string | undefined,
  pageDesigns: Record<string, PageDesign | undefined>,
  apiContracts: DevelopmentPlanningApiContract[]
): RelatedEndpointContext | undefined {
  if (!pageId) return undefined
  const design = pageDesigns[pageId]
  const dependency = design?.api_dependencies?.[0]
  if (!dependency) return undefined
  const contract = apiContracts.find((item) => item.id === dependency.apiContractId)
  if (!contract) return undefined
  const boundEndpointId = design?.response_bindings?.find(
    (binding) => binding.endpointId
  )?.endpointId
  const endpointIndex = contract.endpoints.findIndex(
    (item) =>
      (boundEndpointId && item.id === boundEndpointId) ||
      (item.method === dependency.method && item.path === dependency.path)
  )
  if (endpointIndex < 0) return undefined
  const endpoint = contract.endpoints[endpointIndex]
  return {
    apiContractId: endpoint.apiContractId || contract.id,
    endpointId: endpoint.id || String(endpointIndex + 1),
    endpointLabel: `${endpoint.method} ${endpoint.path}`
  }
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
  applicationPreviewMode,
  onApplicationPreviewModeChange,
  theme,
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  // 设计阶段文档编辑态:editedDesignDocs 存保存后的编辑版(覆盖静态产物显示);
  // 编辑草稿由 DocPanel 内部管理(默认即编辑,IDE 式),保存时经 onSaveEdit(draft) 回传。
  const [editedDesignDocs, setEditedDesignDocs] = useState<
    Partial<Record<WorkspaceDocKey, string>>
  >({})
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  const activeDetailTargetRef = useRef<ActiveDetailTarget>(activeDetailTarget)
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
  const { phase: activeWorkbenchPhase, derivedPhase } = useWorkbenchPhase()
  const [viewingTaskPhase, setViewingTaskPhase] = useState<WorkbenchPhase>(activeWorkbenchPhase)
  // 异步页面会话完成时读取最新目标，避免旧页面挡板覆盖刚打开的应用预览。
  useEffect(() => {
    activeDetailTargetRef.current = activeDetailTarget
  }, [activeDetailTarget])
  // 按当前应用工作区取演示数据（三应用各自独立）。
  const scenario = appDataByWorkspace(application.workspaceRoot)
  const pageDesignCatalog = useMemo(
    () => ({
      ...(scenario.designedPageDesigns as Record<string, PageDesign | undefined>),
      ...(scenario.pageDesigns as Record<string, PageDesign | undefined>)
    }),
    [scenario.designedPageDesigns, scenario.pageDesigns]
  )
  const pageEndpointRelations = useMemo(() => {
    const relations: Record<string, string[]> = {}
    developmentPlanningPages.forEach((page) => {
      const endpoint = resolvePageRelatedEndpoint(
        page.pageId,
        pageDesignCatalog,
        developmentPlanningApiContracts
      )
      if (endpoint) {
        relations[page.pageId] = [`${endpoint.apiContractId}:${endpoint.endpointId}`]
      }
    })
    return relations
  }, [developmentPlanningApiContracts, developmentPlanningPages, pageDesignCatalog])
  // 设计阶段：右侧工作区只显示「文档」tab（spec 文档），预览/源码为开发阶段产物。
  const isDesignPhase = activeWorkbenchPhase === 'product'
  // 审查阶段：应用级单会话，左侧大纲折叠(参照设计阶段)。
  const isReviewPhase = activeWorkbenchPhase === 'test'
  const displayIsDesignPhase = viewingTaskPhase === 'product'
  const displayIsReviewPhase = viewingTaskPhase === 'test'
  const viewingHistoricalStage = viewingTaskPhase !== activeWorkbenchPhase
  // 所有模块(页面+接口)开发完成后直接进入审查阶段。
  // 判定用 designState 的运行时标记(实时可靠)，不用 WorkbenchPage 的 pages.designed state(刷新时序不稳)。
  const launchScenario = appDataByWorkspace(application.workspaceRoot)
  const planningPages = (launchScenario.planningArtifacts.pages || []) as Array<{ pageId?: string }>
  const planningContracts = launchScenario.planningArtifacts.apiContracts || []
  const allDevelopmentModulesComplete =
    planningPages.length > 0 &&
    planningPages.every((page) => isPageDesigned(String(page.pageId || ''))) &&
    planningContracts.every((contract) =>
      contract.endpoints.every((endpoint) =>
        isEndpointDesigned(contract.id, String(endpoint.id || ''))
      )
    )
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
      const nextPreviewUrl = composeVersionPreviewUrl(nextBaseUrl, '/', versionViewKey)
      if (!nextPreviewUrl) return
      setPreviewError('')
      setRuntimePreviewBaseUrl(nextBaseUrl)
      setRuntimePreviewLaunchError('')
      onApplicationPreviewModeChange(true)
    },
    [onApplicationPreviewModeChange, versionViewKey]
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
    createReviewSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
    ensurePlannedPageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleRenameSession,
    handleSelectEndpoint,
    handleSelectPage,
    messages,
    persistSession,
    runningSessionsRef,
    selectedSkills,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({ application, editorMode })

  // 正式应用设计会话优先按标题识别；兼容旧数据时取最早的应用级非审查会话，避免误选新建的临时对话。
  const formalDesignSession = useMemo(() => {
    const candidates = sessions.filter(
      (session) =>
        !session.pageId && !session.endpointId && !(session.title || '').includes('代码审查')
    )
    return (
      candidates.find((session) => (session.title || '') === '应用设计') ||
      candidates.find((session) => (session.title || '').startsWith('设计')) ||
      candidates.sort((a, b) => a.createdAt - b.createdAt)[0]
    )
  }, [sessions])

  const {
    activeWorkflow,
    error,
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
    ensureReviewSession: createReviewSession,
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
    autoStartReview: activeWorkbenchPhase === 'development' && allDevelopmentModulesComplete,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  })

  const applicationPreviewUrl = composeVersionPreviewUrl(runtimePreviewBaseUrl, '/', versionViewKey)

  // 顶部应用配置入口复用现有配置页，不在工作台中增加第二套配置界面。
  const { requestCodeChangeRevert, revertingCodeChangeIds } = useCodeChangeRevert({
    activeSession,
    disabled: loading || workspaceBusy,
    getSessionMessages,
    persistSession,
    rightPanel,
    setRightPanel,
    setSessionMessages
  })

  // 输入框引导语按阶段/任务/迭代单独写（剧本范畴）：设计(新应用 vs 迭代)/审查/开发(页面/接口/应用)。
  const baseChatCopy = chatCopy[editorMode]
  const iterationVersion = (application.versions || []).find(
    (v) => v.id === application.currentVersionId
  )
  const composerPlaceholder = displayIsDesignPhase
    ? iterationVersion?.parentVersionId
      ? '描述本次迭代要补充或调整的需求，例如新增功能、调整流程…'
      : '描述应用的核心场景与业务需求，或确认当前需求文档…'
    : displayIsReviewPhase
      ? '确认审查结果，或输入审查意见…'
      : activeApiEndpoint
        ? '描述想调整的接口，例如参数、返回值、业务逻辑…'
        : activePageOption
          ? '描述想微调的页面，例如修改文案、样式、字段…'
          : '描述应用整体调整，或确认开发计划…'
  const copy = { ...baseChatCopy, placeholder: composerPlaceholder }
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
    : activeDetailTarget.type === 'page'
      ? {
          type: 'page' as const,
          title: activePageTitle,
          path: activePageOption?.path || activePage?.path || '/',
          description:
            activePageOption?.purpose ||
            activePage?.purpose ||
            application.senario ||
            '当前应用页面',
          keyFeatures: activePage?.keyFeatures || []
        }
      : {
          type: 'application' as const,
          title: application.name,
          path: '/',
          description: application.senario || '完整应用预览与版本成果',
          keyFeatures: ['应用级预览', '页面与接口完成情况', '版本成果']
        }
  const latestWorkflowForDisplay = activeWorkflow || latestMessageWorkflow(messages)
  // 固定应用预览始终使用应用根路由，并按当前查看版本渲染快照。
  const previewTabUrl = applicationPreviewUrl
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
    // 历史版本始终以应用级预览为主视图（切版本时强制回应用预览，不受 dismissed 影响），
    // 不让所属阶段的默认文档覆盖版本预览。
    if (state.dismissed) return
    // 设计阶段：右侧固定「文档」区，自动落到第一份已生成产物（需求文档/项目计划/构建任务，tab 渐进可用）。
    // 注意：本 effect 依赖 rightPanel，必须仅在非 doc 或未选中有效文档时才 set，否则每次新建对象 →
    // rightPanel 引用变 → effect 重跑 → 再 set，形成 Maximum update depth 死循环。
    if (displayIsDesignPhase) {
      state.type = 'doc'
      // active docKey 跟随旅程：生成中按 workflow.phase，就绪后按 stage（见 resolveDesignDocKey）。
      const targetKey = resolveDesignDocKey(
        applicationLifecycle?.initialization?.stage,
        activeWorkflow?.summary?.phase,
        activeWorkflow?.summary?.status === 'running'
      )
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
    // 审查阶段：右侧固定「审查报告」tab（报告内容随工作流进度填充，见 docContent）。
    // 已发布/锁定历史版本默认应用预览（versionPreviewOnly 分支已处理，与此互斥）。
    if (displayIsReviewPhase) {
      if (rightPanel?.type === 'preview' && state.dismissed) {
        state.type = 'preview'
        return
      }
      state.type = 'doc'
      if (!rightPanel || rightPanel.type !== 'doc') {
        setRightPanel({ type: 'doc' })
      }
      return
    }
    if (!activePageOption) return
    // 预览在页面开发完成（integration_test 通过）即可展示；launch_project/acceptance 向前兼容。
    const previewLaunched =
      activeWorkflow?.summary?.phase === 'launch_project' ||
      activeWorkflow?.summary?.phase === 'acceptance' ||
      (activeWorkflow?.summary?.phase === 'integration_test' &&
        activeWorkflow?.summary?.status === 'completed')
    // 页面任务默认开「页面预览」（当前页面 path），与应用预览（应用根）区分。
    const workflowPreviewUrl = activeWorkflow?.result?.preview_url
    const launchedPreviewUrl =
      composeVersionPreviewUrl(
        runtimePreviewBaseUrl,
        activeHeaderTarget.path || '/',
        versionViewKey
      ) || (typeof workflowPreviewUrl === 'string' ? workflowPreviewUrl : '')
    if (previewLaunched && launchedPreviewUrl) {
      lastPanelForPageRef.current = activePageId || ''
      // 用户手动切走后不强制覆盖回预览（openWorkspaceTab 会置 dismissed）。
      if (
        !state.dismissed &&
        (rightPanel?.type !== 'preview' || !rightPanel.requestKey?.endsWith(':page'))
      ) {
        state.type = 'preview'
        setRightPanel({
          type: 'preview',
          requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:page`,
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
    applicationPreviewUrl,
    displayIsDesignPhase,
    displayIsReviewPhase,
    versionPreviewOnly,
    versionViewKey,
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
  if (
    activePageOption &&
    (pageDesignGenerated || activePageOption.designed || activePageOption.hasDetailPlan)
  ) {
    pageDesignEverGeneratedRef.current.add(activePageOption.pageId)
  }
  const activePageDesign =
    activePageOption &&
    (activePageOption.designed ||
      activePageOption.hasDetailPlan ||
      pageDesignGenerated ||
      pageDesignEverGeneratedRef.current.has(activePageOption.pageId))
      ? (scenario.pageDesigns as Record<string, PageDesign | undefined>)[activePageOption.pageId] ||
        (scenario.designedPageDesigns as Record<string, PageDesign | undefined>)[
          activePageOption.pageId
        ]
      : undefined
  // 设计阶段：右侧工作区 tab 就是应用级三份产物文档（需求文档 / 项目计划 / 构建任务），
  // 按旅程进度（生命周期 stage）渐进可用——新应用刚进入（澄清中）三份都未生成，tab 置灰；
  // 需求确认后「需求文档」可用，规划确认后「项目计划」可用，进入开发前「构建任务」可用。
  // 数据直接来自 mock 产物（requirement-spec / project-plan），与规划会话共用构建器。
  const designStage = applicationLifecycle?.initialization?.stage
  // 开发产物在单个版本内只解锁一次，避免生命周期事件切换时整组目录闪退再出现。
  const [developmentCatalogUnlocked, setDevelopmentCatalogUnlocked] = useState(() =>
    isDevelopmentCatalogConfirmed(designStage)
  )
  useEffect(() => {
    if (isDevelopmentCatalogConfirmed(designStage)) setDevelopmentCatalogUnlocked(true)
  }, [designStage])
  const designConversationStarted = Boolean(
    displayIsDesignPhase &&
      activeSession &&
      (messages.some((message) => message.role === 'user') ||
        sessionRunStates[activeSession.sessionId])
  )
  const [stableDesignStatuses, setStableDesignStatuses] = useState(() => ({
    'requirement-spec': designArtifactStatus(designStage, 'requirement-spec'),
    'project-plan': designArtifactStatus(designStage, 'project-plan')
  }))
  const [stableDesignAvailability, setStableDesignAvailability] = useState(() => ({
    'requirement-spec': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
    'project-plan': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
  }))
  useEffect(() => {
    const requirementObserved = designArtifactStatus(designStage, 'requirement-spec')
    const projectObserved = designArtifactStatus(designStage, 'project-plan')
    setStableDesignStatuses((current) => ({
      'requirement-spec': advanceArtifactStatus(
        current['requirement-spec'],
        designConversationStarted && requirementObserved === 'not-started'
          ? 'in-progress'
          : requirementObserved
      ),
      'project-plan': advanceArtifactStatus(current['project-plan'], projectObserved)
    }))
    setStableDesignAvailability((current) => ({
      'requirement-spec':
        current['requirement-spec'] ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
      'project-plan':
        current['project-plan'] ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
    }))
  }, [designConversationStarted, designStage])
  const designDocs = (
    [
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
      }
    ] as Array<{ key: WorkspaceDocKey; title: string; path: string; content: string }>
  ).map((doc) => ({
    ...doc,
    available: stableDesignAvailability[doc.key]
  }))
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
  const activeDesignDoc = designDocs.find((doc) => doc.key === activeDesignDocKey)
  const designDocName = displayIsReviewPhase ? '审查报告' : activeDesignDoc?.title
  // 开发阶段：接口详设文档（endpoint-designs.json）；无设计数据时回落 app 级兜底。
  const activeEndpointDesign = activeApiEndpoint
    ? (scenario.endpointDesigns as Record<string, Record<string, unknown> | undefined>)[
        activeApiEndpoint.endpointId
      ]
    : undefined
  const conversationPageOption =
    activePageOption ||
    developmentPlanningPages.find((page) => page.pageId === activeSession?.pageId)
  const conversationPageDesign = conversationPageOption
    ? (scenario.pageDesigns as Record<string, PageDesign | undefined>)[
        conversationPageOption.pageId
      ] ||
      (scenario.designedPageDesigns as Record<string, PageDesign | undefined>)[
        conversationPageOption.pageId
      ]
    : undefined
  const conversationRelatedEndpoint = useMemo(
    () =>
      resolvePageRelatedEndpoint(
        conversationPageOption?.pageId,
        pageDesignCatalog,
        developmentPlanningApiContracts
      ),
    [conversationPageOption?.pageId, developmentPlanningApiContracts, pageDesignCatalog]
  )
  /** 从当前会话恢复它关联的接口，即使用户正从页面视角查看同一跨产物任务。 */
  const conversationEndpointOption = useMemo(() => {
    const apiContractId =
      activeSession?.apiContractId ||
      activeApiEndpoint?.apiContractId ||
      conversationRelatedEndpoint?.apiContractId
    const endpointId =
      activeSession?.endpointId ||
      activeApiEndpoint?.endpointId ||
      conversationRelatedEndpoint?.endpointId
    if (!apiContractId || !endpointId) return undefined
    const contract = developmentPlanningApiContracts.find((item) => item.id === apiContractId)
    const endpoint = contract?.endpoints.find(
      (item, index) => (item.id || String(index + 1)) === endpointId
    )
    return contract && endpoint ? { apiContractId, endpointId, contract, endpoint } : undefined
  }, [
    activeApiEndpoint?.apiContractId,
    activeApiEndpoint?.endpointId,
    activeSession?.apiContractId,
    activeSession?.endpointId,
    conversationRelatedEndpoint?.apiContractId,
    conversationRelatedEndpoint?.endpointId,
    developmentPlanningApiContracts
  ])
  const conversationEndpointDesign = conversationEndpointOption
    ? (scenario.endpointDesigns as Record<string, Record<string, unknown> | undefined>)[
        conversationEndpointOption.endpointId
      ]
    : undefined
  const activeEndpointDocTitle = activeEndpointDesign
    ? `${String(activeEndpointDesign.method || 'GET').toUpperCase()} ${activeEndpointDesign.path || ''}`
    : undefined
  // 文档头部地址栏：设计阶段显示当前产物路径，开发阶段接口显示 method+path。
  const docTitle = displayIsReviewPhase
    ? '代码审查报告'
    : displayIsDesignPhase
      ? activeDesignDoc?.path
      : activeEndpointDocTitle
  // 设计阶段文档生成中：workflow 正在 running 对应文档（需求/项目计划），右栏走生成中占位。
  // 生成完成 stage 推进后该文档就绪（content 立即从静态产物取），docKey 切过去，generating 结束。
  const designPhase = activeWorkflow?.summary?.phase
  const designPhaseRunning = activeWorkflow?.summary?.status === 'running'
  const docGenerating =
    displayIsDesignPhase &&
    designPhaseRunning &&
    (designPhase === 'requirements' || designPhase === 'project_planning')
  // 开发阶段：详细设计生成中（detail_confirmation running，尚未到确认卡），右侧文档区富加载占位。
  const devDetailGenerating =
    !displayIsDesignPhase &&
    activePageOption &&
    workflowForDisplay?.summary?.phase === 'detail_confirmation' &&
    String(workflowForDisplay?.summary?.status) === 'running'
  // 审查报告就绪：子图（lint/安全/健康度）跑完、发 code_review 结果确认卡，或已 finalize。
  // 未就绪时审查报告 tab 显示占位（启动审查前 / 跑动中），就绪后才填充报告内容。
  const reviewReportClarification = (activeWorkflow?.state?.clarification ??
    activeWorkflow?.result?.clarification) as { mode?: string } | undefined
  const reviewReportReady =
    activeWorkflow?.summary?.phase === 'finalize_project' ||
    (activeWorkflow?.summary?.phase === 'code_review' &&
      reviewReportClarification?.mode !== 'review_start')
  // 已完成审查后查看历史设计或开发对话时，报告状态仍应保持完成，不能被当前会话的工作流覆盖。
  const reviewArtifactReady =
    reviewReportReady ||
    (derivedPhase === 'test' &&
      sessions.some(
        (session) => (session.title || '').includes('代码审查') && session.messageCount > 0
      ))
  const docContent = displayIsDesignPhase
    ? ((activeDesignDocKey ? editedDesignDocs[activeDesignDocKey] : undefined) ??
      activeDesignDoc?.content)
    : displayIsReviewPhase
      ? reviewReportReady
        ? buildReviewReport()
        : ''
      : activePageOption
        ? activePageDesign
          ? buildPageDesignDoc(activePageDesign)
          : // 待设计页面：右侧文档区空占位（DocPanel 引导），详细设计生成后才有文档。
            ''
        : activeEndpointDesign
          ? buildEndpointDesignDoc(activeEndpointDesign)
          : ''
  // 一个开发对话可以同时关联页面和接口，右侧分别保留各自的源码与设计文件。
  const pageSource =
    conversationPageOption &&
    (conversationPageOption.designed || conversationPageOption.hasDetailPlan) &&
    conversationPageDesign
      ? buildPageSource(conversationPageDesign, conversationPageOption.pageId || 'page')
      : undefined
  const endpointSource = conversationEndpointDesign
    ? buildEndpointSource(conversationEndpointDesign)
    : undefined
  const [activeArtifactTab, setActiveArtifactTab] = useState<WorkspaceTabKey>('page-source')
  const activeSource =
    activeArtifactTab === 'endpoint-source' ? endpointSource : pageSource || endpointSource
  // 页面任务独立「页面预览」：路由到当前页面 path，与应用预览（应用根）区分。
  const pagePreviewPath = conversationPageOption?.path || activeHeaderTarget.path || '/'
  const pagePreviewUrl = composeVersionPreviewUrl(
    runtimePreviewBaseUrl,
    pagePreviewPath,
    versionViewKey
  )
  const pagePreviewReady = Boolean(
    conversationPageOption &&
      (conversationPageOption.designed || conversationPageOption.hasDetailPlan) &&
      pagePreviewUrl
  )
  const pagePreviewTab: WorkspaceTab = {
    key: 'page-preview',
    label: pagePreviewPath,
    available: pagePreviewReady,
    icon: 'browser'
  }
  const workspaceTabs: WorkspaceTab[] = displayIsDesignPhase
    ? [
        ...(designDocs || []).map((doc) => ({
          key: doc.key,
          label: doc.path.split('/').pop() || doc.path,
          available: doc.available,
          icon: 'document' as const
        }))
      ]
    : displayIsReviewPhase
      ? [{ key: 'doc', label: 'code-review.md', available: true, icon: 'document' }]
      : [
          ...(conversationPageOption
            ? [
                pagePreviewTab,
                {
                  key: 'page-source' as const,
                  label:
                    pageSource?.filePath.split('/').pop() || `${conversationPageOption.pageId}.tsx`,
                  available: Boolean(pageSource),
                  icon: 'code' as const
                }
              ]
            : []),
          ...(conversationEndpointOption
            ? [
                {
                  key: 'endpoint-source' as const,
                  label:
                    endpointSource?.filePath.split('/').pop() ||
                    `${conversationEndpointOption.endpointId}.java`,
                  available: Boolean(endpointSource),
                  icon: 'code' as const
                }
              ]
            : []),
          ...(conversationPageDesign || conversationEndpointDesign
            ? [
                {
                  key: 'detail-doc' as const,
                  label: `${conversationPageOption?.pageId || conversationEndpointOption?.endpointId || 'detail-design'}.md`,
                  available: true,
                  icon: 'document' as const
                }
              ]
            : [])
        ]
  const conversationDesignTitle =
    sessions.find((session) => session.id === activeSessionId)?.title || '开发对话'
  const artifactDocContent = buildConversationDesignDoc({
    title: conversationDesignTitle,
    pageDesign: conversationPageDesign,
    endpointDesign: conversationEndpointDesign
  })
  const resolvedDocContent =
    displayIsDesignPhase || displayIsReviewPhase ? docContent : artifactDocContent
  const resolvedDocTitle =
    displayIsDesignPhase || displayIsReviewPhase
      ? docTitle
      : `plans/conversations/${conversationPageOption?.pageId || conversationEndpointOption?.endpointId || 'detail-design'}.md`
  // 当前激活的工作区 tab：设计阶段 = 当前产物 docKey，其余按 rightPanel 类型。
  const activeWorkspaceTab: WorkspaceTabKey =
    rightPanel?.type === 'doc'
      ? ((rightPanel.docKey ||
          (activeArtifactTab.endsWith('-doc') ? activeArtifactTab : 'doc')) as WorkspaceTabKey)
      : rightPanel?.type === 'preview'
        ? rightPanel.requestKey?.endsWith(':page')
          ? ('page-preview' as WorkspaceTabKey)
          : 'preview'
        : rightPanel?.type === 'source'
          ? activeArtifactTab.endsWith('-source')
            ? activeArtifactTab
            : pageSource
              ? 'page-source'
              : 'endpoint-source'
          : ((rightPanel?.type || 'doc') as WorkspaceTabKey)
  const openWorkspaceTab = (key: WorkspaceTabKey): void => {
    // 手动切换 tab 后不再自动升级/重开（含预览强制覆盖回切）。
    autoOpenStateRef.current.type = null
    autoOpenStateRef.current.dismissed = true
    if (key === 'page-preview' && pagePreviewUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:page`,
        url: pagePreviewUrl
      })
    } else if (key === 'preview' && previewTabUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:application`,
        url: previewTabUrl
      })
    } else if (key === 'doc') {
      setRightPanel({ type: 'doc' })
    } else if (key === 'source') {
      setRightPanel({ type: 'source' })
    } else if (key === 'page-source' || key === 'endpoint-source') {
      setActiveArtifactTab(key)
      setRightPanel({ type: 'source' })
    } else if (key === 'detail-doc') {
      setActiveArtifactTab(key)
      setRightPanel({ type: 'doc' })
    } else if (WORKSPACE_DOC_KEYS.has(key)) {
      setRightPanel({ type: 'doc', docKey: key as WorkspaceDocKey })
    }
  }
  const activeConversationStarted = Boolean(
    activeSession &&
      (messages.some((message) => message.role === 'user') ||
        sessionRunStates[activeSession.sessionId])
  )
  const conversationArtifactsBase: ConversationArtifact[] = displayIsDesignPhase
    ? (designDocs || []).map((doc) => ({
        id: documentArtifactId(doc.key),
        name: doc.path.split('/').pop() || doc.path,
        path: doc.path,
        status:
          stableDesignStatuses[doc.key] === 'completed'
            ? '已完成'
            : stableDesignStatuses[doc.key] === 'in-progress'
              ? '进行中'
              : '未开始',
        type: 'document'
      }))
    : displayIsReviewPhase
      ? [
          {
            id: documentArtifactId('code-review'),
            name: 'code-review.md',
            path: 'reports/code-review.md',
            status: reviewArtifactReady ? '已完成' : '进行中',
            type: 'document'
          }
        ]
      : [
          ...(conversationPageOption
            ? [
                {
                  id: pageArtifactId(conversationPageOption.pageId),
                  name: conversationPageOption.label,
                  path: conversationPageOption.path,
                  status:
                    conversationPageOption.designed || conversationPageOption.hasDetailPlan
                      ? ('已完成' as const)
                      : activeConversationStarted && activeSession?.pageId
                        ? ('进行中' as const)
                        : ('未开始' as const),
                  type: 'page' as const
                }
              ]
            : []),
          ...(conversationEndpointOption
            ? [
                {
                  id: endpointArtifactId(
                    conversationEndpointOption.apiContractId,
                    conversationEndpointOption.endpointId
                  ),
                  name: `${conversationEndpointOption.endpoint.method} ${conversationEndpointOption.endpoint.path}`,
                  path: conversationEndpointOption.endpoint.path,
                  status:
                    conversationEndpointOption.endpoint.designed ||
                    conversationEndpointOption.endpoint.hasDetailPlan
                      ? ('已完成' as const)
                      : activeConversationStarted && activeSession?.endpointId
                        ? ('进行中' as const)
                        : ('未开始' as const),
                  type: 'endpoint' as const
                }
              ]
            : [])
        ]
  const artifactCatalog: WorkbenchArtifact[] = [
    ...designDocs.map((doc) => ({
      id: documentArtifactId(doc.key),
      name: doc.title,
      path: doc.path,
      phase: 'product' as const,
      status: stableDesignStatuses[doc.key],
      type: 'document' as const,
      available: doc.available
    })),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningPages.map((page) => ({
          id: pageArtifactId(page.pageId),
          name: page.label,
          path: page.path,
          phase: 'development' as const,
          status:
            page.designed || page.hasDetailPlan
              ? ('completed' as const)
              : sessions.some(
                    (session) =>
                      session.pageId === page.pageId &&
                      (session.messageCount > 0 || sessionRunStates[session.id])
                  )
                ? ('in-progress' as const)
                : ('not-started' as const),
          type: 'page' as const,
          available: true
        }))
      : []),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningApiContracts.flatMap((contract) =>
          contract.endpoints.map((endpoint, endpointIndex) => {
            const apiContractId = endpoint.apiContractId || contract.id
            const endpointId = endpoint.id || String(endpointIndex + 1)
            return {
              id: endpointArtifactId(apiContractId, endpointId),
              name: `${endpoint.method} ${endpoint.path}`,
              path: endpoint.path,
              phase: 'development' as const,
              status:
                endpoint.designed || endpoint.hasDetailPlan
                  ? ('completed' as const)
                  : sessions.some(
                        (session) =>
                          (session.messageCount > 0 || sessionRunStates[session.id]) &&
                          artifactIdsForSession(session, pageEndpointRelations).includes(
                            endpointArtifactId(apiContractId, endpointId)
                          )
                      )
                    ? ('in-progress' as const)
                    : ('not-started' as const),
              type: 'endpoint' as const,
              available: true
            }
          })
        )
      : []),
    {
      id: documentArtifactId('code-review'),
      name: '审查报告',
      path: 'reports/code-review.md',
      phase: 'test',
      status:
        derivedPhase !== 'test' ? 'not-started' : reviewArtifactReady ? 'completed' : 'in-progress',
      type: 'document',
      available: derivedPhase === 'test'
    }
  ]
  const artifactOwners = resolveArtifactOwners(
    sessions.map((session) => ({
      artifactIds: artifactIdsForSession(session, pageEndpointRelations),
      createdAt: session.createdAt,
      sessionId: session.id
    }))
  )
  const artifactAccessById = Object.fromEntries(
    artifactCatalog.map((artifact) => [
      artifact.id,
      resolveArtifactAccess({
        artifact,
        currentPhase: activeWorkbenchPhase,
        currentSessionId: activeSessionId,
        ownerSessionId: artifactOwners[artifact.id],
        reachedPhase: derivedPhase,
        versionLocked: versionReadOnly
      })
    ])
  ) as Record<string, WorkbenchArtifactAccess>
  const composerArtifactResources = artifactCatalog.map((artifact) => ({
    accessMessage: artifactAccessById[artifact.id].message,
    accessMode: artifactAccessById[artifact.id].mode,
    id: artifact.id,
    name: artifact.name,
    path: artifact.path,
    type: artifact.type
  }))
  const artifactById = new Map(artifactCatalog.map((artifact) => [artifact.id, artifact]))
  const activeSessionSummary = sessions.find((session) => session.id === activeSessionId)
  const activeSessionArtifactIds = activeSessionSummary
    ? artifactIdsForSession(activeSessionSummary, pageEndpointRelations)
    : []
  const conversationArtifacts: ConversationArtifact[] = (
    activeSessionArtifactIds.length > 0
      ? activeSessionArtifactIds
          .map((artifactId) => artifactById.get(artifactId))
          .filter((artifact): artifact is WorkbenchArtifact => Boolean(artifact))
          .map((artifact) => ({
            id: artifact.id,
            name: artifact.name,
            path: artifact.path,
            status:
              artifact.status === 'completed'
                ? ('已完成' as const)
                : artifact.status === 'in-progress'
                  ? ('进行中' as const)
                  : ('未开始' as const),
            type: artifact.type === 'model' ? ('document' as const) : artifact.type
          }))
      : conversationArtifactsBase
  ).map((artifact) => ({
    ...artifact,
    accessMessage: artifactAccessById[artifact.id]?.message,
    accessMode: artifactAccessById[artifact.id]?.mode
  }))
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
        ? {
            type: 'page' as const,
            label: activePageOption.label,
            path: activePageOption.path,
            purpose: activePageOption.purpose
          }
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
  const lockedEndpoint =
    lockedDetailTarget?.type === 'endpoint'
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
    const requestedTarget = activeDetailTargetRef.current
    if (requestedTarget.type === 'page' && requestedTarget.pageId === session.pageId) return
    if (
      requestedTarget.type === 'endpoint' &&
      requestedTarget.apiContractId === session.apiContractId &&
      requestedTarget.endpointId === session.endpointId
    ) {
      return
    }
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

  /** 使用当前前端端口和所选页面路由打开独立全屏预览窗口（预留，当前未挂载）。 */

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
    onApplicationPreviewModeChange(false)
    setPreviewError('')
    // 应用配置只是覆盖中间工作区的临时抽屉，不改变用户对右侧产物面板的开关选择。
    setActiveView('settings')
  }

  /** 从目录恢复任意历史会话，并同步其任务级别而不改变顶部阶段状态。 */
  const handleOpenSidebarSession = async (sessionId: string): Promise<void> => {
    const session = sessions.find((item) => item.id === sessionId)
    onApplicationPreviewModeChange(false)
    if (session?.pageId || session?.endpointId) setViewingTaskPhase('development')
    else if ((session?.title || '').includes('代码审查')) setViewingTaskPhase('test')
    else if ((session?.title || '').includes('应用设计')) setViewingTaskPhase('product')
    else setViewingTaskPhase(activeWorkbenchPhase)
    setActiveView('chat')
    await handleOpenChatSession(sessionId)
  }

  /** 新建普通对话时退出页面/API 目标上下文，避免后续消息被旧目标接管。 */
  const handleCreateChatSession = (): void => {
    onApplicationPreviewModeChange(false)
    setViewingTaskPhase(activeWorkbenchPhase)
    setActiveView('chat')
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleCreateSessionFromList()
  }

  /** 从产物菜单为页面创建任务对话，并同步进入该页面工作上下文。 */
  const handleCreatePageTask = (page: DevelopmentPlanningPageOption): void => {
    onApplicationPreviewModeChange(false)
    setViewingTaskPhase('development')
    setActiveView('chat')
    setActiveDetailTarget({ type: 'page', pageId: page.pageId })
    void createPageSession(
      page.pageId,
      page.label,
      resolvePageRelatedEndpoint(page.pageId, pageDesignCatalog, developmentPlanningApiContracts)
    )
  }

  /** 从产物菜单为接口创建任务对话，并同步进入该接口工作上下文。 */
  const handleCreateEndpointTask = (target: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }): void => {
    onApplicationPreviewModeChange(false)
    setViewingTaskPhase('development')
    setActiveView('chat')
    setActiveDetailTarget({
      type: 'endpoint',
      apiContractId: target.apiContractId,
      endpointId: target.endpointId,
      endpointKey: `${target.apiContractId}:${target.endpointId}`,
      label: target.endpointLabel
    })
    void createEndpointSession(target.apiContractId, target.endpointId, target.endpointLabel)
  }

  /** 从应用大纲切换页面；没有消息历史时仅展示空白上下文，不提前创建会话。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    onApplicationPreviewModeChange(false)
    setViewingTaskPhase('development')
    setPreviewError('')
    const completedPreviewUrl =
      page.designed || page.hasDetailPlan
        ? composeVersionPreviewUrl(runtimePreviewBaseUrl, page.path, versionViewKey)
        : ''
    if (completedPreviewUrl) {
      // 历史阶段只查看已完成页面，不依赖当前 Agent 工作流再次产出 preview_url。
      autoOpenStateRef.current.type = null
      autoOpenStateRef.current.dismissed = true
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:${page.pageId}:page`,
        url: completedPreviewUrl
      })
      onRightPanelOpenChange(true)
    } else {
      setRightPanel(undefined)
    }
    setActiveView('chat')
    setInteractingDetailTargetKey(pageDetailTargetKey(page.pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId: page.pageId })
    const ownerSessionId = artifactOwners[pageArtifactId(page.pageId)]
    if (ownerSessionId) {
      // 页面与接口可能共用同一个默认对话；按产物写锁打开唯一所有者。
      handleOpenSession(ownerSessionId).catch(() => undefined)
      return
    }
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
    const relatedEndpoint = resolvePageRelatedEndpoint(
      firstUndesigned.pageId,
      pageDesignCatalog,
      developmentPlanningApiContracts
    )
    // 计划里的首个正式开发对话预先落目录；用户发送后再把关联产物推进为进行中。
    void ensurePlannedPageSession(firstUndesigned.pageId, firstUndesigned.label, relatedEndpoint)
  }, [
    activeWorkbenchPhase,
    isDesignPhase,
    activeDetailTarget.type,
    activeApiEndpoint,
    developmentPlanningPages,
    developmentPlanningApiContracts,
    ensurePlannedPageSession,
    pageDesignCatalog
  ])

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
        // 待设计页面进入详细设计前默认展示文档占位，固定应用预览仍保留在页签中。
        const latestTarget = activeDetailTargetRef.current
        if (latestTarget.type === 'page' && latestTarget.pageId === pageId) {
          setRightPanel({ type: 'doc' })
        }
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
  }, [
    activeWorkbenchPhase,
    isDesignPhase,
    displayedPlanExecutionMode,
    lockedDetailTarget,
    activePageOption
  ])

  // 阶段切换会话隔离：切到设计阶段恢复产品 Agent 的设计会话（无 pageId/endpointId），
  // 切回开发阶段恢复当前页面会话。避免切阶段后对话内容仍是上一阶段、只换了 Agent 名。
  const phaseSwitchHandledRef = useRef<WorkbenchPhase | ''>('')
  useEffect(() => {
    if (phaseSwitchHandledRef.current === activeWorkbenchPhase) return
    phaseSwitchHandledRef.current = activeWorkbenchPhase
    setViewingTaskPhase(activeWorkbenchPhase)
    if (isDesignPhase) {
      // 设计阶段：激活产品 Agent 的设计会话(无归属且非审查会话)。
      if (formalDesignSession) {
        handleOpenChatSession(formalDesignSession.id).catch(() => undefined)
      }
    } else if (isReviewPhase) {
      // 审查阶段只恢复唯一的代码审查会话。
      const reviewSession = sessions
        .filter(
          (session) =>
            !session.pageId && !session.endpointId && (session.title || '').includes('代码审查')
        )
        .sort((a, b) => b.updatedAt - a.updatedAt)[0]
      // 当前迭代进入审查时默认展示审查报告；应用预览由顶部独立入口承载。
      setRightPanel({ type: 'doc' })
      if (reviewSession) {
        handleOpenChatSession(reviewSession.id).catch(() => undefined)
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
  }, [activeWorkbenchPhase, isDesignPhase, isReviewPhase])

  /** 授权条「放弃」:丢弃该文档编辑版,回到 Agent 生成版。 */
  const handleDiscardArtifact = (docKey: WorkspaceDocKey): void => {
    setEditedDesignDocs((prev) => {
      if (!(docKey in prev)) return prev
      const next = { ...prev }
      delete next[docKey]
      return next
    })
  }

  /** 从文档产物树打开文档，并切换到该产物所属的默认应用级对话。 */
  const handleDesignArtifactSelect = async (
    docKey: WorkspaceDocKey | 'code-review'
  ): Promise<void> => {
    setActiveView('chat')
    setViewingTaskPhase(docKey === 'code-review' ? 'test' : 'product')
    autoOpenStateRef.current.dismissed = true
    setRightPanel(docKey === 'code-review' ? { type: 'doc' } : { type: 'doc', docKey })
    onRightPanelOpenChange(true)
    const ownerSessionId = artifactOwners[documentArtifactId(docKey)]
    const defaultSession = sessions.find((session) => session.id === ownerSessionId)
    if (defaultSession && defaultSession.id !== activeSessionId) {
      await handleOpenChatSession(defaultSession.id)
    }
  }

  /** 从文档产物菜单创建草稿对话，并保持文档阶段上下文。 */
  const handleCreateDocumentTask = (docKey: WorkspaceDocKey | 'code-review'): void => {
    handleCreateChatSession()
    setViewingTaskPhase(docKey === 'code-review' ? 'test' : 'product')
    setRightPanel(docKey === 'code-review' ? { type: 'doc' } : { type: 'doc', docKey })
  }

  /** 从应用大纲切换 API；页面和 API 目标互斥，因此会清空当前页面选中态。 */
  const handleApiEndpointSelect = (target: ActiveApiEndpointTarget): void => {
    onApplicationPreviewModeChange(false)
    setViewingTaskPhase('development')
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey(endpointDetailTargetKey(target.apiContractId, target.endpointId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ ...target, type: 'endpoint' })
    // 接口可能与页面共用同一个开发对话；优先显式打开关联会话，不能为接口另起空对话。
    const ownerSessionId =
      artifactOwners[endpointArtifactId(target.apiContractId, target.endpointId)]
    const relatedSession = sessions.find((session) => session.id === ownerSessionId)
    if (relatedSession) {
      // 保留“接口产物”选中态，只复用它的页面任务会话，避免旧会话身份把目标切回页面。
      handleOpenSession(relatedSession.id).catch(() => undefined)
      return
    }
    handleSelectEndpoint(target.apiContractId, target.endpointId).catch(() => undefined)
  }

  /** 统计开发产物完成数；完成当前产物后自动推进到首个未开始产物。 */
  const completedDevelopmentArtifactCount =
    developmentPlanningPages.filter((page) => page.designed || page.hasDetailPlan).length +
    developmentPlanningApiContracts.reduce(
      (total, contract) =>
        total +
        contract.endpoints.filter((endpoint) => endpoint.designed || endpoint.hasDetailPlan).length,
      0
    )
  const completionProgressRef = useRef({ versionKey: '', count: 0 })
  useEffect(() => {
    const previous = completionProgressRef.current
    completionProgressRef.current = {
      versionKey: versionViewKey,
      count: completedDevelopmentArtifactCount
    }
    if (
      previous.versionKey !== versionViewKey ||
      activeWorkbenchPhase !== 'development' ||
      completedDevelopmentArtifactCount <= previous.count
    ) {
      return
    }

    const activeArtifactCompleted = activePageOption
      ? Boolean(activePageOption.designed || activePageOption.hasDetailPlan)
      : activeApiEndpointOption
        ? Boolean(
            activeApiEndpointOption.endpoint.designed ||
              activeApiEndpointOption.endpoint.hasDetailPlan
          )
        : false
    if (!activeArtifactCompleted) return

    const nextPage = developmentPlanningPages.find((page) => !page.designed && !page.hasDetailPlan)
    if (nextPage) {
      handlePageSelect(nextPage)
      return
    }
    for (const contract of developmentPlanningApiContracts) {
      const endpointIndex = contract.endpoints.findIndex(
        (endpoint) => !endpoint.designed && !endpoint.hasDetailPlan
      )
      if (endpointIndex < 0) continue
      const endpoint = contract.endpoints[endpointIndex]
      const endpointId = endpoint.id || String(endpointIndex + 1)
      const apiContractId = endpoint.apiContractId || contract.id
      handleApiEndpointSelect({
        apiContractId,
        endpointId,
        endpointKey: `${apiContractId}:${endpointId}`,
        label: `${endpoint.method} ${endpoint.path}`
      })
      return
    }
    // 完成数增长时只推进一次，避免状态回放反复抢占用户当前选择。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedDevelopmentArtifactCount, versionViewKey])

  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<void> => {
    const targetKey = pageDetailTargetKey(pageId)
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    setActiveDetailTarget({ type: 'page', pageId })
    const relatedEndpoint = resolvePageRelatedEndpoint(
      pageId,
      pageDesignCatalog,
      developmentPlanningApiContracts
    )
    const started = await handleStartDetailConfirmation(
      pageId,
      pageLabel,
      hasDetailPlan,
      templateParams,
      relatedEndpoint
    )
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
            templateSourcePath: targetContext.templateSourcePath
          }
        : undefined
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

  // PlanExecutionDock 移除后，查看计划 / 结构化确认交互暂未挂载（保留节点卡入口）。

  /** 用户关闭技能后立即清理当前会话草稿中的同名标签。 */
  const handleSkillDisabled = (skillName: string): void => {
    const nextSkills = selectedSkills.filter((skill) => skill.name !== skillName)
    if (nextSkills.length !== selectedSkills.length) {
      setSelectedSkillsByKey(draftKey, nextSkills)
    }
  }

  const showRightPanel =
    !applicationPreviewMode && activeView === 'chat' && rightPanelOpen && Boolean(rightPanel)
  const showDevelopmentTasks = developmentPlanningReady && developmentCatalogUnlocked
  const activeEditableArtifactId = displayIsDesignPhase
    ? activeDesignDocKey
      ? documentArtifactId(activeDesignDocKey)
      : ''
    : displayIsReviewPhase
      ? documentArtifactId('code-review')
      : activeArtifactTab.startsWith('endpoint') && conversationEndpointOption
        ? endpointArtifactId(
            conversationEndpointOption.apiContractId,
            conversationEndpointOption.endpointId
          )
        : conversationPageOption
          ? pageArtifactId(conversationPageOption.pageId)
          : ''
  const activeArtifactAccess = activeEditableArtifactId
    ? artifactAccessById[activeEditableArtifactId]
    : undefined
  const artifactEditorReadOnly = !activeArtifactAccess || activeArtifactAccess.mode !== 'write'
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
      {applicationPreviewMode ? (
        <main className={cx('application-preview-workspace')}>
          <BrowserPreviewPanel
            applicationMode
            application={application}
            pages={developmentPlanningPages}
            requestKey={`${versionViewKey}:${runtimePreviewBaseUrl}:application-workspace`}
            requestedUrl={applicationPreviewUrl}
            previewBaseUrl={runtimePreviewBaseUrl}
            selectedPagePath="/"
            errorMessage={runtimePreviewLaunchError}
            onInspectingChange={setElementInspectionActive}
          />
        </main>
      ) : (
        <>
          <SessionSidebar
            activeSessionId={activeSessionId}
            apiContracts={developmentPlanningApiContracts}
            applicationName={application.name}
            artifactAccessById={artifactAccessById}
            deletingSessionId={deletingSessionId}
            designArtifacts={[
              ...designDocs.map((doc) => ({
                available: doc.available,
                key: doc.key,
                label: doc.title,
                path: doc.path,
                status: stableDesignStatuses[doc.key]
              })),
              {
                available: derivedPhase === 'test',
                key: 'code-review' as const,
                label: '审查报告',
                path: 'reports/code-review.md',
                status:
                  derivedPhase !== 'test'
                    ? ('not-started' as const)
                    : reviewArtifactReady
                      ? ('completed' as const)
                      : ('in-progress' as const)
              }
            ]}
            filesActive={activeView === 'files'}
            fixedOpen
            onApiEndpointSelect={handleApiEndpointSelect}
            onCreateEndpointTask={handleCreateEndpointTask}
            onCreateDocumentTask={handleCreateDocumentTask}
            onCreatePageTask={handleCreatePageTask}
            onCreateSession={handleCreateChatSession}
            onDeleteSession={handleDeleteSession}
            onDesignArtifactSelect={handleDesignArtifactSelect}
            onOpenSession={handleOpenSidebarSession}
            onRenameSession={handleRenameSession}
            onPageSelect={handlePageSelect}
            onShowFiles={handleShowFiles}
            onShowSettings={handleShowSettings}
            onShowSkills={handleShowSkills}
            pages={developmentPlanningPages}
            pageEndpointRelations={pageEndpointRelations}
            pageTree={developmentPlanningPageTree}
            readOnly={versionReadOnly}
            selectedApiEndpointKey={
              activeApiEndpoint?.endpointKey ||
              (activeDetailTarget.type === 'none' &&
              activeSession?.apiContractId &&
              activeSession.endpointId
                ? `${activeSession.apiContractId}:${activeSession.endpointId}`
                : '')
            }
            selectedDesignArtifactKey={
              displayIsReviewPhase && rightPanel?.type === 'doc'
                ? 'code-review'
                : activeDesignDocKey ||
                  (displayIsDesignPhase && activeSession
                    ? stableDesignStatuses['project-plan'] === 'in-progress'
                      ? 'project-plan'
                      : 'requirement-spec'
                    : undefined)
            }
            selectedPageId={
              activePageId ||
              (activeDetailTarget.type === 'none' ? activeSession?.pageId || '' : '')
            }
            sessionRunStates={sessionRunStates}
            sessions={sessions}
            showDevelopmentTasks={showDevelopmentTasks}
            skillsActive={activeView === 'skills'}
            settingsActive={activeView === 'settings'}
            theme={theme}
          />
          <div className={cx('ai-chat-assistant')}>
            {activeView === 'skills' ? (
              <SkillsPage onSkillDisabled={handleSkillDisabled} theme={theme} />
            ) : activeView === 'files' ? (
              <AgentFilesPage />
            ) : activeView === 'settings' ? (
              <SettingsPage
                application={application}
                onClose={() => setActiveView('chat')}
                onSaved={onApplicationUpdate}
              />
            ) : (
              <div className={cx('ai-chat-main')}>
                {activeView === 'chat' ? (
                  <PageContextHeader
                    artifacts={conversationArtifacts}
                    conversationTitle={
                      sessions.find((session) => session.id === activeSessionId)?.title ||
                      (displayIsDesignPhase
                        ? '应用设计'
                        : displayIsReviewPhase
                          ? '代码审查'
                          : '新对话')
                    }
                    historical={viewingHistoricalStage}
                  />
                ) : null}
                {viewingHistoricalStage && activeView === 'chat' ? (
                  <div className={cx('historical-task-notice')} role="status">
                    <span>
                      正在查看历史阶段任务，顶部仍处于
                      {activeWorkbenchPhase === 'product'
                        ? '设计'
                        : activeWorkbenchPhase === 'development'
                          ? '开发'
                          : '审查'}
                      阶段。
                    </span>
                    <button type="button" onClick={() => setViewingTaskPhase(activeWorkbenchPhase)}>
                      返回当前任务
                    </button>
                  </div>
                ) : null}
                {previewError && (
                  <Alert
                    className={cx('preview-action-error')}
                    message={previewError}
                    showIcon
                    type="error"
                  />
                )}

                <MessageList
                  agentPhase={viewingTaskPhase}
                  applicationLifecycle={applicationLifecycle}
                  codeChangeActionsDisabled={
                    loading || workspaceBusy || versionReadOnly || viewingHistoricalStage
                  }
                  interactionsDisabled={versionReadOnly || viewingHistoricalStage}
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

                <ChatComposer
                  activeWorkflow={activeWorkflow}
                  artifactResources={composerArtifactResources}
                  copy={copy}
                  draft={draft}
                  error={error}
                  loading={loading}
                  onDraftChange={(value) => setDraftByKey(draftKey, value)}
                  onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                  onSend={handleSend}
                  onStopGenerating={handleStopGenerating}
                  readOnly={versionReadOnly || viewingHistoricalStage}
                  readOnlyMessage={
                    viewingHistoricalStage
                      ? '历史阶段任务仅供查看；如需调整，请从顶部切换应用阶段'
                      : '已生成版本只读，请先发起新迭代或回退后继续调整'
                  }
                  stopping={stopping}
                  selectedSkills={selectedSkills}
                  workspaceBusy={workspaceBusy}
                  workspaceRoot={workspaceRoot}
                />
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
                      selectedPagePath={
                        conversationPageOption?.path ||
                        (activeHeaderTarget.type === 'page' ? activeHeaderTarget.path : '/')
                      }
                      errorMessage={runtimePreviewLaunchError}
                      onInspectingChange={setElementInspectionActive}
                    />
                  )}
                  {rightPanel.type === 'doc' && (
                    <DocPanel
                      content={resolvedDocContent}
                      docName={designDocName}
                      generating={docGenerating || devDetailGenerating}
                      readOnly={artifactEditorReadOnly}
                      onSaveEdit={
                        artifactEditorReadOnly || !displayIsDesignPhase
                          ? undefined
                          : (draft) => {
                              if (activeDesignDocKey) {
                                setEditedDesignDocs((prev) => ({
                                  ...prev,
                                  [activeDesignDocKey]: draft
                                }))
                              }
                            }
                      }
                      title={resolvedDocTitle}
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
        </>
      )}
    </section>
  )
}

import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench } from '../../context'
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
import PageContextHeader from './components/PageContextHeader'
import type { PageContextStatus } from './components/PageContextHeader'
import PlanExecutionDock from './components/PlanExecutionDock'
import SessionSidebar from './components/SessionSidebar'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import DetailConfirmationPageSelector from '../DetailConfirmationPageSelector'
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
  onReturnWelcome: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
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
  onReturnWelcome,
  onThemeChange,
  theme
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  const [interactingDetailTargetKey, setInteractingDetailTargetKey] = useState('')
  const [generatingDetailTargetKey, setGeneratingDetailTargetKey] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  const { publishAiMessage } = useWorkbench()
  const {
    assistantPanelWidth,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    splitDragging
  } = useAssistantPreviewLayout()
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
  const showPreviewActions = editorMode === 'frontend'
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
  const activeEndpointSelectorTarget = activeApiEndpoint
    ? {
        ...activeApiEndpoint,
        hasDetailPlan: Boolean(
          activeApiEndpointOption?.endpoint.designed ||
            activeApiEndpointOption?.endpoint.hasDetailPlan
        ),
        path: activeApiEndpointOption?.endpoint.path,
        purpose: activeApiEndpointOption?.endpoint.summary
      }
    : undefined
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

  /** 在右侧工作区打开当前页面预览。 */
  const handleOpenPage = (): void => {
    const targetUrl = composePreviewUrl(runtimePreviewBaseUrl, activeHeaderTarget.path)
    if (!targetUrl) {
      setPreviewError(runtimePreviewLaunchError || '前端服务尚未启动完成，暂时无法预览页面')
      return
    }
    setPreviewError('')
    setRightPanel({
      type: 'preview',
      requestKey: `${runtimePreviewBaseUrl}:${activeHeaderTarget.path}`,
      url: targetUrl
    })
  }

  /** 关闭右侧工作区的页面预览。 */
  const handleClosePage = (): void => {
    setRightPanel(undefined)
  }

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

  /** 根据弹框里选择的目标类型启动页面或接口详细设计。 */
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
    await handleSubmitClarification(workflow, answers)
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

  return (
    <section
      className={cx(
        'ai-chat-panel',
        rightPanelOpen && 'embedded-preview-open',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <div className={cx('ai-chat-assistant')}>
        <SessionSidebar
          activeSessionId={activeSessionId}
          application={application}
          deletingSessionId={deletingSessionId}
          loadingSessions={loadingSessions}
          outlineLocked={detailTargetSelectionRequired}
          onCreateSession={handleCreateChatSession}
          onCreatePageSession={handleCreatePageSession}
          onCreateEndpointSession={handleCreateEndpointSession}
          onDeleteSession={handleDeleteSession}
          onApiEndpointSelect={handleApiEndpointSelect}
          onOpenSession={handleOpenChatSession}
          onPageSelect={handlePageSelect}
          onReturnWelcome={onReturnWelcome}
          onShowFiles={handleShowFiles}
          onShowSettings={handleShowSettings}
          onShowSkills={handleShowSkills}
          onThemeChange={onThemeChange}
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
          theme={theme}
          workspaceRoot={workspaceRoot}
        />

        {activeView === 'skills' ? (
          <SkillsPage onSkillDisabled={handleSkillDisabled} theme={theme} />
        ) : activeView === 'files' ? (
          <AgentFilesPage />
        ) : activeView === 'settings' ? (
          <SettingsPage application={application} onSaved={onApplicationUpdate} />
        ) : detailProgressVisible ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              apiContracts={developmentPlanningApiContracts}
              disabled={loading || workspaceBusy}
              generating
              loading={false}
              onStart={handleStartDetailDesign}
              pages={developmentPlanningPages}
              pageTree={developmentPlanningPageTree}
              selectedEndpoint={activeEndpointSelectorTarget}
              selectedPage={activeApiEndpoint ? undefined : activePageOption}
              workflowEvents={activeWorkflow?.events}
            />
          </div>
        ) : detailTargetSelectionRequired ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              apiContracts={developmentPlanningApiContracts}
              disabled={loading || workspaceBusy}
              generating={loading}
              loading={!developmentPlanningReady}
              onStart={handleStartDetailDesign}
              pages={developmentPlanningPages}
              pageTree={developmentPlanningPageTree}
              selectedEndpoint={activeApiEndpoint}
              workflowEvents={activeWorkflow?.events}
            />
          </div>
        ) : (
          <div className={cx('ai-chat-main')}>
            <PageContextHeader
              description={activeHeaderTarget.description}
              isPageOpen={activeHeaderTarget.type === 'page' && rightPanel?.type === 'preview'}
              keyFeatures={activeHeaderTarget.keyFeatures}
              lastAnalyzedAt={activeSessionUpdatedAt}
              onClosePage={handleClosePage}
              onOpenFullscreenPage={handleOpenFullscreenPreview}
              onOpenPage={handleOpenPage}
              pagePath={activeHeaderTarget.path}
              pageTitle={activeHeaderTarget.title}
              previewAvailable={showPreviewActions && Boolean(runtimePreviewBaseUrl)}
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
              messages={messages}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onRevertCodeChanges={requestCodeChangeRevert}
              onSubmitClarification={handleSubmitWorkflowClarification}
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

            {(requiresPageDetailDesign(activePageOption) ||
              requiresEndpointDetailDesign(activeApiEndpointOption?.endpoint)) &&
            displayedPlanExecutionMode === 'idle' &&
            !detailConfirmationWaitingReview ? (
              <DetailConfirmationPageSelector
                disabled={loading || workspaceBusy}
                generating={false}
                loading={false}
                mode="locked"
                onStart={handleStartDetailDesign}
                pages={developmentPlanningPages}
                selectedEndpoint={activeEndpointSelectorTarget}
                selectedPage={activePageOption}
                workflowEvents={activeWorkflow?.events}
              />
            ) : null}
          </div>
        )}
      </div>

      {rightPanelOpen && (
        <div
          aria-label="拖动调整右侧面板宽度"
          aria-orientation="vertical"
          aria-valuenow={assistantPanelWidth}
          className={cx('panel-split-handle', splitDragging && 'dragging')}
          onKeyDown={handlePanelSplitKeyDown}
          onMouseDown={handlePanelSplitDragStart}
          role="separator"
          tabIndex={0}
          title="拖动调整左右面板宽度"
        >
          <HolderOutlined className={cx('panel-split-handle-icon')} />
        </div>
      )}

      {rightPanel?.type === 'preview' && (
        <div className={cx('embedded-preview-pane')}>
          <BrowserPreviewPanel
            application={application}
            pages={developmentPlanningPages}
            requestKey={rightPanel.requestKey}
            requestedUrl={rightPanel.url}
            previewBaseUrl={runtimePreviewBaseUrl}
            selectedPagePath={activeHeaderTarget.type === 'page' ? activeHeaderTarget.path : '/'}
            errorMessage={runtimePreviewLaunchError}
          />
        </div>
      )}

      {rightPanel?.type === 'diff' && (
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

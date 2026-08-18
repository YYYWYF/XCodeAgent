import { HolderOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, message } from 'antd'
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
  WorkflowClarificationAnswers,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import { CLASS_PREFIX, composePreviewUrl, cx, openPreviewWindow, previewOrigin } from '../../utils'
import { readWorkspaceFile } from '../../service/workspaceTools'
import { saveRequirementSpecDraft } from '../../service/applicationPagePlanning'
import { isAuthenticationFailure } from '../../service/authentication'
import { formatError } from '../Welcome/utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import DocPanel from './components/DocPanel'
import SourcePanel from './components/SourcePanel'
import UiDesignPreviewPanel from './components/UiDesignPreviewPanel'
import MessageList from './components/MessageList'
import PageContextHeader from './components/PageContextHeader'
import type { PageContextStatus } from './components/PageContextHeader'
import PlanExecutionDock from './components/PlanExecutionDock'
import RightPanelTabs, { type WorkspaceTab, type WorkspaceTabKey } from './components/RightPanelTabs'
import SessionSidebar from './components/SessionSidebar'
import WorkspaceDebugDock from './components/WorkspaceDebugDock'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import { workflowClarification } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import DetailConfirmationPageSelector from '../DetailConfirmationPageSelector'
import PageDesignProgress from '../DetailConfirmationPageSelector/PageDesignProgress'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { chatCopy } from './constants'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  requiresEndpointDetailDesign,
  requiresInitialDetailDesignSelection,
  sessionDetailTargetKey,
  workflowDetailTargetKey,
  type WorkflowPreviewTarget
} from './utils'
import {
  isConversationWaitingForInput,
  isConversationWorkflow,
  type ChatInputMode
} from './conversationMode'
import {
  deriveDisplayedPlanExecutionMode,
  planExecutionShowsDebugResume,
  planExecutionContextForEndpoint,
  planExecutionContextForPage,
  shouldRenderPlanExecutionDock,
  workflowCanRetryFailedTasks,
  workflowResumeNode,
  type PlanExecutionMode
} from './planExecutionMode'
import './AiChatPanel.less'

// 把规划确认答案转成可读的用户操作文案，用于对话区留痕。
// 不同 mode 对应不同的操作语义：确认全部设计稿/确认保存需求文档/进入项目规划等。
function planningUserMessageText(answers: WorkflowClarificationAnswers): string {
  const entries = Object.entries(answers)
  if (entries.length === 0) return ''
  // UI 设计稿单页动作（换一换/选模板/调整）：不作为用户消息留痕（卡片内已体现操作）。
  if ('ui_design_action' in answers) return ''
  const lines: string[] = []
  for (const [key, value] of entries) {
    const text = planningAnswerToText(value)
    if (!text) continue
    const label = PLANNING_ANSWER_LABELS[key] || key
    lines.push(`${label}：${text}`)
  }
  return lines.join('\n')
}

const PLANNING_ANSWER_LABELS: Record<string, string> = {
  ui_design_confirmation: 'UI 设计稿确认',
  requirement_spec_confirmation: '需求文档确认',
  requirement_spec_feedback: '需求文档意见',
  product_plan_confirmation: '产品规划确认',
  technical_plan_confirmation: '技术规划确认',
  project_plan_confirmation: '项目计划确认',
  detail_review: '详细设计确认'
}

function planningAnswerToText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map((v) => planningAnswerToText(v)).join('、')
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    // 结构化答案取常见字段
    if (typeof record.message === 'string') return record.message
    if (typeof record.text === 'string') return record.text
    if (typeof record.instruction === 'string') return record.instruction
    // 选择题答案：selected + other
    if (record.selected !== undefined) {
      const selected = planningAnswerToText(record.selected)
      const other = typeof record.other === 'string' && record.other.trim() ? `（${record.other}）` : ''
      return `${selected}${other}`
    }
    return JSON.stringify(value)
  }
  return String(value || '')
}

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
  previewLaunchLoading: boolean
  onReturnWelcome: () => void
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string
  ) => void
  onThemeChange: (theme: 'light' | 'dark') => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  /** 模板生成失败后重试（重新触发模板生成）。 */
  onRetryTemplate?: () => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  planningThreadId?: string
  planningWorkflow?: WorkflowRunPayload
  theme: 'light' | 'dark'
  rightPanelOpen: boolean
  onRightPanelOpenChange: (open: boolean) => void
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
function latestMessageWorkflow(
  messages: Array<{ workflow?: WorkflowRunPayload }>
): WorkflowRunPayload | undefined {
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

/** 将用户已经点击开始设计的页面状态同步到菜单树，避免树节点覆盖页面列表状态。 */
function applyStartedPageDesignToTree(
  nodes: DevelopmentPlanningPageTreeNode[],
  startedPageIds: ReadonlySet<string>
): DevelopmentPlanningPageTreeNode[] {
  return nodes.map((node) => {
    if (node.type === 'menu') {
      return {
        ...node,
        children: applyStartedPageDesignToTree(node.children || [], startedPageIds)
      }
    }
    const pageId = String(node.pageId || node.key || '').trim()
    return startedPageIds.has(pageId) ? { ...node, designed: true } : node
  })
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
  previewLaunchLoading,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onThemeChange,
  onPlanningStreamReady,
  onRetryTemplate,
  generatingTemplate,
  planningThreadId,
  planningWorkflow,
  theme,
  rightPanelOpen,
  onRightPanelOpenChange
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  // 记录用户是否主动进入自由对话，用于切换工作台上下文和设计入口状态。
  const [freeChatSelected, setFreeChatSelected] = useState(false)
  // 按页面/API目标保留输入模式，切换会话时不让用户反复选择同一模式。
  const [inputModes, setInputModes] = useState<Record<string, ChatInputMode>>({})
  const [interactingDetailTargetKey, setInteractingDetailTargetKey] = useState('')
  const [generatingDetailTargetKey, setGeneratingDetailTargetKey] = useState('')
  // 仅在用户点击页面开始设计后记录本次工作台内的页面设计状态。
  const [startedPageDesignIds, setStartedPageDesignIds] = useState<Set<string>>(
    () => new Set()
  )
  const [previewError, setPreviewError] = useState('')
  const [elementInspectionActive, setElementInspectionActive] = useState(false)
  // UI 设计稿预览：右侧"UI设计稿"tab 当前选中的页面 id（由中间区卡片或右侧列表驱动）。
  const [uiDesignActivePageId, setUiDesignActivePageId] = useState('')
  // UI 设计稿：当前正在执行单页动作（选模板/换一换）的 pageId，用于右侧预览显示加载态。
  const [uiDesignActionPageId, setUiDesignActionPageId] = useState<string | null>(null)
  // 设计阶段右侧文档：当 confirmationArtifact 不可用（确认完成后）时，从磁盘异步读取
  // 需求文档/项目计划文件内容，避免确认后右侧 tab 显示"待生成"。
  const [designDocFileContent, setDesignDocFileContent] = useState<Record<string, string>>({})
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  const { publishAiMessage } = useWorkbench()
  const { phase: activeWorkbenchPhase, switchPhase } = useWorkbenchPhase()
  const isDesignPhase = activeWorkbenchPhase === 'product'
  // 模板生成完成后（lifecycle 变为 ready_for_workbench），derivedPhase 自动变 development。
  // 前端拦截：保持 product 阶段，等用户点"进入开发"按钮后才放开（switchPhase(null) 恢复跟随旅程）。
  // 用 sessionStorage 按 applicationId 记录用户是否已确认进入开发，跨重挂载保持。
  // 关键：必须检查 lifecycle.initialization.stage === 'ready_for_workbench'，而非仅 derivedPhase=development。
  // 因为 deriveWorkbenchPhase 在 lifecycle 未加载时也默认返回 'development'（workbenchPhase.ts:114），
  // 仅凭 derivedPhase 会误触发拦截，导致后续真正完成时 ref 已置位、gate 不再出现。
  const lifecycleReadyForWorkbench =
    applicationLifecycle?.initialization?.stage === 'ready_for_workbench'
  const enterDevConfirmedKey = `xcodeagent:enter-dev-confirmed:${application.id}`
  const [enterDevConfirmed, setEnterDevConfirmed] = useState(
    () => window.localStorage.getItem(enterDevConfirmedKey) === '1'
  )
  // 只在 lifecycle 首次到达 ready_for_workbench 时锁一次，不依赖 activeWorkbenchPhase（避免覆盖用户切换）。
  const planningConfirmedSeenRef = useRef(false)
  useEffect(() => {
    if (!lifecycleReadyForWorkbench) return
    if (planningConfirmedSeenRef.current) return
    planningConfirmedSeenRef.current = true
    if (enterDevConfirmed) return
    // 后端已完成模板生成（lifecycle=ready_for_workbench）：锁住 product 阶段，等用户手动进入开发。
    switchPhase('product')
  }, [lifecycleReadyForWorkbench, enterDevConfirmed, switchPhase])

  const {
    assistantPanelRatio,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    setRightPanel,
    splitDragging
  } = useAssistantPreviewLayout({ rightPanelOpen })
  // 中间区卡片点"查看设计稿"时：切到右侧"UI设计稿"tab 并选中该页。
  const handleUiDesignActivePageChange = useCallback((pageId: string) => {
    setUiDesignActivePageId(pageId)
    if (rightPanel?.type !== 'doc' || rightPanel.docKey !== 'ui-design') {
      setRightPanel({ type: 'doc', docKey: 'ui-design' })
    }
  }, [rightPanel, setRightPanel])
  // 右侧面板实际展示：外部开关 + 面板有内容。开关由 WorkbenchPage 顶栏控制，
  // 面板内容（preview/doc/diff）由本组件按目标类型设置。
  const showRightPanel = rightPanelOpen && Boolean(rightPanel)

  // ---- 设计阶段（product）：右侧展示需求文档/产品规划/技术规划 ----
  // 文档内容来自真实规划 workflow（planningWorkflow），不照搬 prototype 的 mock。
  // 需求确认后需求文档可用（confirmationArtifact.id=requirement_spec），
  // 产品规划确认阶段直接展示当前 ProductPlan Markdown。
  const planningArtifact = planningWorkflow?.confirmationArtifact
  // UI 设计稿：从规划 workflow 的 clarification（ui_design_confirmation 模式）或
  // state/result 的 ui_designs 读取页面列表。设计稿生成中或已就绪都算可用。
  const planningClarification = planningWorkflow?.summary?.clarification as
    | { mode?: string; status?: string; pages?: unknown[] }
    | undefined
  // 产物文档只在对应确认阶段才视为可用：需求填表（requirements clarification）阶段
  // 虽然后端已生成 artifact，但用户尚未确认，不应在右侧展示产物详情。
  const requirementDocAvailable =
    planningArtifact?.id === 'requirement_spec' &&
    Boolean(planningArtifact.content) &&
    planningClarification?.mode === 'requirement_spec_confirmation'
  const productPlanDocAvailable =
    planningArtifact?.id === 'product_plan' &&
    Boolean(planningArtifact.content) &&
    planningClarification?.mode === 'product_plan_confirmation'
  const technicalPlanDocAvailable =
    planningArtifact?.id === 'technical_plan' &&
    Boolean(planningArtifact.content) &&
    planningClarification?.mode === 'technical_plan_confirmation'
  const planningPhaseRunning = planningWorkflow?.summary?.status === 'running'
  const planningPhase = planningWorkflow?.summary?.phase
  const planningUiDesignPagesSource: unknown[] | undefined =
    Array.isArray(planningClarification?.pages) && planningClarification!.pages!.length > 0
      ? planningClarification!.pages
      : (planningWorkflow?.state?.ui_designs as { pages?: unknown[] } | undefined)?.pages ??
        (planningWorkflow?.result?.ui_designs as { pages?: unknown[] | undefined } | undefined)?.pages
  // UI 设计稿页面列表（右侧"UI设计稿"tab 预览用）。
  // workflow running 期间流式快照可能丢失 page.code，用 ref 缓存上一次有 code 的 pages，
  // running 期间回退到缓存，避免右侧设计稿闪烁消失、tab 被误禁用。
  const uiDesignPagesCacheRef = useRef<Array<{ pageId?: string; name?: string; code?: string; status?: string; template_id?: string }>>([])
  const uiDesignPages = useMemo(() => {
    const raw = (Array.isArray(planningUiDesignPagesSource)
      ? planningUiDesignPagesSource.filter((p) => p && typeof p === 'object')
      : []) as Array<{ pageId?: string; name?: string; code?: string; status?: string; template_id?: string }>
    if (raw.some((p) => Boolean(p.code))) {
      uiDesignPagesCacheRef.current = raw
      return raw
    }
    if (planningPhaseRunning && uiDesignPagesCacheRef.current.length > 0) {
      return uiDesignPagesCacheRef.current
    }
    return raw
  }, [planningUiDesignPagesSource, planningPhaseRunning])
  // UI 设计稿可用：设计稿已生成就算可用（不限制 clarification.mode），
  // 避免项目规划阶段（mode=project_planning）UI设计稿 tab 被误禁用。
  // 用 uiDesignPages（含 running 期间缓存）判断，避免流式快照丢失时 tab 误禁用。
  const uiDesignAvailable = isDesignPhase && uiDesignPages.length > 0
  const designDocs = isDesignPhase
    ? ([
        {
          key: 'requirement-spec' as WorkspaceDocKey,
          title: '需求文档',
          path: 'specs/requirement-spec.md',
          content: requirementDocAvailable
            ? planningArtifact?.content || ''
            : designDocFileContent['requirement-spec'] || '',
          available: requirementDocAvailable || Boolean(designDocFileContent['requirement-spec'])
        },
        {
          key: 'product-plan' as WorkspaceDocKey,
          title: '产品规划',
          path: 'plans/product-plan.md',
          content: productPlanDocAvailable
            ? planningArtifact?.content || ''
            : designDocFileContent['product-plan'] || '',
          available: productPlanDocAvailable || Boolean(designDocFileContent['product-plan']) || planningPhase === 'product_planning'
        },
        {
          key: 'technical-plan' as WorkspaceDocKey,
          title: '技术规划',
          path: 'plans/technical-plan.md',
          content: technicalPlanDocAvailable
            ? planningArtifact?.content || ''
            : designDocFileContent['technical-plan'] || '',
          available: technicalPlanDocAvailable || Boolean(designDocFileContent['technical-plan']) || planningPhase === 'technical_planning'
        },
        {
          key: 'ui-design' as WorkspaceDocKey,
          title: 'UI设计稿',
          path: 'specs/ui-designs',
          content: '',
          available: uiDesignAvailable
        }
      ] as Array<{ key: WorkspaceDocKey; title: string; path: string; content: string; available: boolean }>)
    : undefined
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
  const activeDesignDoc = designDocs?.find((doc) => doc.key === activeDesignDocKey)
  // 开发阶段：右侧文档区无设计阶段产物，显示引导文案（选中页面/端点后由后续逻辑填充）。
  const designDocContent = isDesignPhase
    ? (activeDesignDoc?.content || '')
    : '从左侧大纲选择页面或接口，查看设计文档。'
  const designDocName = isDesignPhase ? activeDesignDoc?.title : undefined
  const designDocTitle = isDesignPhase ? activeDesignDoc?.path : undefined
  const uiDesignActivePage =
    uiDesignPages.find((p) => (p.pageId || '') === uiDesignActivePageId) || uiDesignPages[0]
  const uiDesignActivePageCode = uiDesignActivePage?.code || ''
  // 设计阶段文档生成中：规划 workflow 正在 running 且对应阶段（需求/项目计划）。
  // 文档加载态按当前 tab 区分：需求文档 tab 只在 requirements 阶段生成中，
  // 产品/技术规划 tab 只在对应阶段生成中，避免其他文档误显示加载态。
  const designDocGenerating =
    isDesignPhase &&
    planningPhaseRunning &&
    ((activeDesignDocKey === 'requirement-spec' && planningPhase === 'requirements') ||
      (activeDesignDocKey === 'product-plan' && planningPhase === 'product_planning') ||
      (activeDesignDocKey === 'technical-plan' && planningPhase === 'technical_planning'))
  // UI 设计稿生成中：UI 确认阶段 workflow running（换一换/选模板/首次生成）。
  const uiDesignGenerating =
    isDesignPhase && planningPhaseRunning && planningPhase === 'ui_confirmation'
  // workflow 真正回到待确认状态（requires_user_input）且经历过 running 后才清除单页动作标记。
  // 换一换提交瞬间 planningWorkflow 还是旧的（requires_user_input），此时不应清除；
  // 必须等 workflow 进入 running（动作开始执行）再回到 requires_user_input（动作完成）才清除。
  const planningUiDesignAwaiting =
    planningClarification?.mode === 'ui_design_confirmation' &&
    planningClarification?.status === 'requires_user_input'
  const uiDesignActionStartedRef = useRef(false)
  useEffect(() => {
    if (planningPhaseRunning && uiDesignActionPageId !== null) {
      uiDesignActionStartedRef.current = true
    }
    if (planningUiDesignAwaiting && uiDesignActionStartedRef.current && uiDesignActionPageId !== null) {
      uiDesignActionStartedRef.current = false
      setUiDesignActionPageId(null)
    }
  }, [planningUiDesignAwaiting, planningPhaseRunning, uiDesignActionPageId])
  // 当前选中页面的规划配置。必须提前到 workspaceTabs / 读取源码的 useEffect 之前，
  // 否则这些位置（尤其 useEffect 依赖数组）会在 const 暂时性死区里访问未初始化的
  // activePageOption，抛 ReferenceError: Cannot access 'activePageOption' before initialization。
  const displayedPlanningPages = useMemo(
    () =>
      startedPageDesignIds.size === 0
        ? developmentPlanningPages
        : developmentPlanningPages.map((page) =>
            startedPageDesignIds.has(page.pageId) ? { ...page, designed: true } : page
          ),
    [developmentPlanningPages, startedPageDesignIds]
  )
  const displayedPlanningPageTree = useMemo(
    () => applyStartedPageDesignToTree(developmentPlanningPageTree, startedPageDesignIds),
    [developmentPlanningPageTree, startedPageDesignIds]
  )
  const activePageId = activeDetailTarget.type === 'page' ? activeDetailTarget.pageId : ''
  const activePageOption = useMemo(
    () => displayedPlanningPages.find((page) => page.pageId === activePageId),
    [activePageId, displayedPlanningPages]
  )
  const workspaceTabs: WorkspaceTab[] = isDesignPhase
    ? (designDocs || []).map((doc) => ({ key: doc.key, label: doc.title, available: doc.available }))
    : [
        { key: 'preview', label: '预览', available: Boolean(runtimePreviewBaseUrl) },
        { key: 'source', label: '源码', available: Boolean(activePageOption) },
        { key: 'doc', label: '文档', available: true }
      ]
  const activeWorkspaceTab: WorkspaceTabKey = isDesignPhase
    ? activeDesignDocKey || 'requirement-spec'
    : rightPanel?.type === 'preview'
      ? 'preview'
      : rightPanel?.type === 'source'
        ? 'source'
        : 'doc'
  const openWorkspaceTab = useCallback(
    (key: WorkspaceTabKey) => {
      if (isDesignPhase) {
        setRightPanel({ type: 'doc', docKey: key as WorkspaceDocKey })
      } else if (key === 'preview') {
        setRightPanel({ type: 'preview' })
      } else if (key === 'source') {
        setRightPanel({ type: 'source' })
      } else if (key === 'doc') {
        setRightPanel({ type: 'doc' })
      }
    },
    [isDesignPhase, setRightPanel]
  )
  // 进入开发阶段时重置右侧面板：设计阶段的 doc/docKey 布局切换为开发阶段的预览/文档。
  useEffect(() => {
    if (isDesignPhase) return
    if (!rightPanelOpen) return
    // 设计阶段遗留的 rightPanel（带 docKey）在开发阶段无效，重置为默认文档。
    if (rightPanel?.type === 'doc' && 'docKey' in rightPanel && rightPanel.docKey) {
      setRightPanel({ type: 'doc' })
      return
    }
    // 开发阶段首次进入且右侧面板未设置：默认打开文档 tab。
    if (!rightPanel) {
      setRightPanel({ type: 'doc' })
    }
  }, [isDesignPhase, rightPanelOpen, rightPanel, setRightPanel])
  // 设计阶段首次进入或文档就绪时自动打开右侧文档面板。
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    if (rightPanel?.type === 'doc' || rightPanel?.type === 'preview') return
    const firstAvailable = designDocs?.find((doc) => doc.available)
    if (firstAvailable) {
      setRightPanel({ type: 'doc', docKey: firstAvailable.key })
    } else if (!rightPanel) {
      setRightPanel({ type: 'doc', docKey: 'requirement-spec' })
    }
  }, [isDesignPhase, rightPanelOpen, rightPanel, designDocs, setRightPanel])

  // 需求文档确认阶段：需求文档生成后自动切到"需求文档"tab 展示内容。
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    if (planningClarification?.mode !== 'requirement_spec_confirmation') return
    if (!requirementDocAvailable) return
    if (rightPanel?.type === 'doc' && rightPanel.docKey === 'requirement-spec') return
    setRightPanel({ type: 'doc', docKey: 'requirement-spec' })
  }, [isDesignPhase, rightPanelOpen, planningClarification, requirementDocAvailable, rightPanel, setRightPanel])

  // UI 设计稿首次可用时自动切到"UI设计稿"tab（进入 UI 确认阶段）。
  // 用 ref 标记是否已自动切过，避免用户切回需求文档后被反复切走。
  const uiDesignAutoOpenedRef = useRef(false)
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen || !uiDesignAvailable) return
    if (uiDesignAutoOpenedRef.current) return
    uiDesignAutoOpenedRef.current = true
    setRightPanel({ type: 'doc', docKey: 'ui-design' })
  }, [isDesignPhase, rightPanelOpen, uiDesignAvailable, setRightPanel])

  // 产品规划确认阶段自动切到"产品规划"tab，展示生成的产品规划文档。
  // 用 ref 标记是否已自动切过，避免用户切回需求文档后被反复切回项目计划。
  const productPlanAutoOpenedRef = useRef(false)
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    if (planningClarification?.mode !== 'product_plan_confirmation') return
    if (!productPlanDocAvailable) return
    if (productPlanAutoOpenedRef.current) return
    productPlanAutoOpenedRef.current = true
    if (rightPanel?.type === 'doc' && rightPanel.docKey === 'product-plan') return
    setRightPanel({ type: 'doc', docKey: 'product-plan' })
  }, [isDesignPhase, rightPanelOpen, planningClarification, productPlanDocAvailable, rightPanel, setRightPanel])

  // 技术规划确认阶段自动打开技术规划文档，保持最后一个确认门可见。
  const technicalPlanAutoOpenedRef = useRef(false)
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    if (planningClarification?.mode !== 'technical_plan_confirmation') return
    if (!technicalPlanDocAvailable) return
    if (technicalPlanAutoOpenedRef.current) return
    technicalPlanAutoOpenedRef.current = true
    if (rightPanel?.type === 'doc' && rightPanel.docKey === 'technical-plan') return
    setRightPanel({ type: 'doc', docKey: 'technical-plan' })
  }, [isDesignPhase, rightPanelOpen, planningClarification, technicalPlanDocAvailable, rightPanel, setRightPanel])

  // 确认完成后 confirmationArtifact 不再返回（后端只在 requires_user_input 时返回），
  // 但文件已落盘。从 workflow summary.artifacts 读取路径，异步读文件内容填充右侧 tab。
  useEffect(() => {
    if (!isDesignPhase || !planningWorkflow) return
    const artifacts = planningWorkflow.summary?.artifacts || {}
    const workspaceRoot = application.workspaceRoot || ''
    // 需求文档：confirmationArtifact 不可用时从磁盘读
    const reqPath = String(artifacts.requirement_spec_path || '').trim()
    const reqNeedFile = reqPath && !requirementDocAvailable
    // 产品/技术规划：confirmationArtifact 不可用时从磁盘读
    const productPlanPath = String(artifacts.product_plan_path || '').trim()
    const technicalPlanPath = String(artifacts.technical_plan_path || '').trim()
    const productPlanNeedFile = productPlanPath && !productPlanDocAvailable
    const technicalPlanNeedFile = technicalPlanPath && !technicalPlanDocAvailable
    if (!reqNeedFile && !productPlanNeedFile && !technicalPlanNeedFile) return
    let cancelled = false
    const readFiles = async (): Promise<void> => {
      const next: Record<string, string> = {}
      try {
        if (reqNeedFile) {
          const result = await readWorkspaceFile({ workspace_root: workspaceRoot, path: reqPath })
          if (result.content) next['requirement-spec'] = result.content
        }
      } catch { /* 文件可能尚未生成，静默 */ }
      try {
        if (productPlanNeedFile) {
          const result = await readWorkspaceFile({ workspace_root: workspaceRoot, path: productPlanPath })
          if (result.content) next['product-plan'] = result.content
        }
      } catch { /* 文件可能尚未生成，静默 */ }
      try {
        if (technicalPlanNeedFile) {
          const result = await readWorkspaceFile({ workspace_root: workspaceRoot, path: technicalPlanPath })
          if (result.content) next['technical-plan'] = result.content
        }
      } catch { /* 文件可能尚未生成，静默 */ }
      if (!cancelled && Object.keys(next).length > 0) {
        setDesignDocFileContent((current) => ({ ...current, ...next }))
      }
    }
    void readFiles()
    return () => { cancelled = true }
  }, [isDesignPhase, planningWorkflow, application.workspaceRoot, requirementDocAvailable, productPlanDocAvailable, technicalPlanDocAvailable])

  const activeApiEndpoint = activeDetailTarget.type === 'endpoint' ? activeDetailTarget : undefined
  const activeTargetKey = detailTargetKey(activeDetailTarget)
  const inputModeKey = activeTargetKey || 'free-chat'
  const inputMode: ChatInputMode =
    inputModes[inputModeKey] || (activeDetailTarget.type === 'none' ? 'conversation' : 'design')
  const activePreviewPath = activePageOption?.path || '/'
  const conversationEnabled =
    activeDetailTarget.type === 'none'
      ? true
      : activeApiEndpoint
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
    ensurePlanningSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleSelectEndpoint,
    handleSelectFreeChat,
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
    setSessionMessages,
    clearActiveSession
  } = useChatSessions({
    application,
    editorMode,
    // 开发阶段切换页面/接口会话时保留右侧面板（源码/预览/文档随选中目标自动更新），
    // 仅设计阶段切换规划会话时清空右侧文档面板。
    onCloseRightPanel: () => {
      if (isDesignPhase) setRightPanel(undefined)
    },
    designPhasePlanning: isDesignPhase
  })

  const {
    activeWorkflow,
    conversationRunning,
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
    planEnded,
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
    conversationEnabled,
    inputMode,
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

  // 设计阶段：激活规划会话（绑定 planningThreadId），并注册流式注入句柄，
  // 让 AppEntryPage 把 Modal 转发的 onContent/onWorkflow 注入当前 session 的 messages。
  const planningSessionKeyRef = useRef<string>('')
  // sessionKey 就绪前缓存的流式 chunk，就绪后回放，避免最早的规划消息丢失。
  const pendingPlanningChunksRef = useRef<Array<{ content?: string; workflow?: WorkflowRunPayload }>>([])
  // onPlanningStreamReady 注册的注入句柄，保存需求文档草稿后用它把更新后的
  // workflow 注入回规划会话，驱动右侧需求文档 tab 实时刷新编辑后的内容。
  const planningStreamInjectRef = useRef<((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null>(null)
  // 用户提交规划确认后置 true，下一次 workflow chunk 到达时新增消息卡片（新一轮），
  // 而非覆盖上一轮的对话卡片。同 runId 续跑也能正确区分轮次。
  const planningNewRoundRef = useRef(false)
  // 规划消息自增 id 计数器，避免同一毫秒内新增多条消息导致 React key 重复。
  const planningMessageIdRef = useRef(0)
  // 用 ref 持有最新的 session 操作函数，避免 effect 依赖它们导致循环。
  const getSessionMessagesRef = useRef(getSessionMessages)
  const setSessionMessagesRef = useRef(setSessionMessages)
  const activeSessionIdRef = useRef(activeSessionId)
  const persistSessionRef = useRef(persistSession)
  const activeSessionRef = useRef(activeSession)
  getSessionMessagesRef.current = getSessionMessages
  setSessionMessagesRef.current = setSessionMessages
  activeSessionIdRef.current = activeSessionId
  persistSessionRef.current = persistSession
  activeSessionRef.current = activeSession
  // 规划消息落盘防抖：workflow 到达稳定点（requires_user_input/completed）时延迟落盘，
  // 避免频繁写文件；切换工作区或重启后可从磁盘恢复历史对话。
  const persistPlanningTimerRef = useRef<number | undefined>(undefined)
  const persistPlanningSession = useCallback((sessionKey: string) => {
    if (persistPlanningTimerRef.current !== undefined) {
      window.clearTimeout(persistPlanningTimerRef.current)
    }
    persistPlanningTimerRef.current = window.setTimeout(() => {
      persistPlanningTimerRef.current = undefined
      const identity = activeSessionRef.current
      console.log('[planning-persist] timer fire sessionKey=', sessionKey.slice(-12), 'activeKey=', identity?.key?.slice(-12), 'match=', identity?.key === sessionKey)
      if (!identity || identity.key !== sessionKey) return
      const msgs = getSessionMessagesRef.current(sessionKey)
      console.log('[planning-persist] msgs=', msgs.length)
      if (!msgs.length) return
      void persistSessionRef.current({
        editorMode: identity.editorMode,
        messages: msgs,
        sessionId: identity.sessionId,
        threadId: identity.threadId,
        apiContractId: identity.apiContractId,
        endpointId: identity.endpointId,
        endpointLabel: identity.endpointLabel,
        pageId: identity.pageId,
        titleFrom: '产品 Agent'
      }).catch((error) => console.warn('[planning-persist] failed', error))
    }, 800)
  }, [])

  // 卸载时清掉未触发的落盘定时器，避免在已卸载组件上写状态。
  useEffect(() => {
    return () => {
      if (persistPlanningTimerRef.current !== undefined) {
        window.clearTimeout(persistPlanningTimerRef.current)
        persistPlanningTimerRef.current = undefined
      }
    }
  }, [])

  // 把用户的规划操作（确认/放弃/填表/进入下一阶段）作为一条 user 消息留痕到对话区，
  // 让用户能看到自己每一步操作的痕迹，而非只看到产品 Agent 的回复。
  // answers 是结构化确认答案，转成可读文案后追加为 user 消息。
  // 同时追加一条 assistant 占位消息（loading 态），让用户看到产品 Agent 正在处理，
  // 避免操作后界面像卡死一样无反馈；后续流式 chunk 到达时更新该占位消息。
  const appendPlanningUserMessage = useCallback((answers?: WorkflowClarificationAnswers) => {
    const sessionKey = planningSessionKeyRef.current
    if (!sessionKey || !answers) return
    const text = planningUserMessageText(answers)
    if (!text) return
    const userMessageId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
    const assistantPlaceholderId = userMessageId + 1
    setSessionMessagesRef.current(sessionKey, (prev) => [
      ...prev,
      {
        id: userMessageId,
        role: 'user',
        content: text,
        createdAt: userMessageId
      },
      {
        id: assistantPlaceholderId,
        role: 'assistant',
        content: '',
        planningLoading: true,
        createdAt: assistantPlaceholderId
      }
    ])
  }, [])

  // 把一条流式 chunk 注入指定 session 的 messages（构造/更新 assistant 消息）。
  // 同一轮规划的流式 chunk 更新最后一条 assistant 消息；
  // 新一轮（用户提交确认后，或 runId 变化）新增消息卡片，保留历史对话。
  const injectPlanningChunk = (sessionKey: string, chunk: { content?: string; workflow?: WorkflowRunPayload }): void => {
    const currentMessages = getSessionMessagesRef.current(sessionKey)
    const lastMessage: AgentChatMessage | undefined = currentMessages[currentMessages.length - 1]
    const messageId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
    const lastWorkflowRunId = lastMessage?.workflow?.runId
    const chunkWorkflowRunId = chunk.workflow?.runId
    // 判断是否属于同一轮规划：runId 一致且未提交过新轮。
    const sameRun =
      !planningNewRoundRef.current &&
      lastMessage?.role === 'assistant' &&
      Boolean(lastWorkflowRunId) &&
      Boolean(chunkWorkflowRunId) &&
      lastWorkflowRunId === chunkWorkflowRunId
    // UI 确认阶段的单页动作（换一换/选模板/调整）会触发新 runId，且中途流式快照可能
    // 丢失 clarification.mode。只要最后一条消息是 ui_design_confirmation 卡片且未显式
    // 开启新轮，就视为同轮更新，更新同一张卡片而非新增。
    const lastClarificationMode = lastMessage?.workflow?.summary?.clarification as
      | { mode?: string }
      | undefined
    const sameUiDesignConfirmation =
      !planningNewRoundRef.current &&
      lastMessage?.role === 'assistant' &&
      lastClarificationMode?.mode === 'ui_design_confirmation'

    if (chunk.workflow) {
      // 新一轮 content 先于 workflow 到达时，content 已新增为无 workflow 的消息；
      // workflow 到达时应合并到该消息，而非再新增一条。
      const mergeIntoContentOnly =
        !sameRun &&
        !sameUiDesignConfirmation &&
        lastMessage?.role === 'assistant' &&
        !lastWorkflowRunId &&
        (planningNewRoundRef.current || currentMessages.length > 0)
      if (sameRun || sameUiDesignConfirmation || mergeIntoContentOnly) {
        // 同一轮或合并 content-only 消息：更新最后一条消息的 workflow 与 content。
        planningNewRoundRef.current = false
        // 判断该 chunk 是否带来实质内容（待确认/已完成/有文本/规划阶段 running）。
        // 纯 running 进度快照（如 requirements running、null running）不清除占位 loading，
        // 否则 loading 态被过早清除而卡片又不显示（running 无 requires_user_input），
        // 导致界面卡死。保持 loading 直到真正可交互的 workflow 到达。
        const chunkRequiresInput =
          workflowClarification(chunk.workflow)?.status === 'requires_user_input'
        const chunkCompleted = chunk.workflow.summary?.status === 'completed'
        const chunkHasContent = Boolean(chunk.content?.trim())
        const chunkIsPlanningRunning =
          chunk.workflow.summary?.phase === 'project_planning' &&
          chunk.workflow.summary?.status === 'running'
        const hasSubstantiveWorkflow =
          chunkRequiresInput || chunkCompleted || chunkHasContent || chunkIsPlanningRunning ||
          (['product_planning', 'technical_planning'].includes(String(chunk.workflow.summary?.phase || '')) &&
            chunk.workflow.summary?.status === 'running')
        setSessionMessagesRef.current(sessionKey, (prev) => {
          const updated = [...prev]
          const prevMessage = updated[updated.length - 1]
          updated[updated.length - 1] = {
            ...prevMessage,
            workflow: chunk.workflow,
            content: chunk.content ?? prevMessage.content,
            planningLoading: hasSubstantiveWorkflow ? false : prevMessage.planningLoading
          }
          return updated
        })
      } else {
        // 新一轮：纯进度快照（content 空、无待确认 clarification）不创建消息卡片，
        // 避免空白卡片占位；待 content 或 clarification 到达后再创建。
        // 例外：规划阶段 running 期间创建卡片显示生成加载态，
        // 让用户看到规划进度，而非长时间无反馈。
        const hasContent = Boolean(chunk.content?.trim())
        const requiresInput =
          workflowClarification(chunk.workflow)?.status === 'requires_user_input'
        const isPlanningRunning =
          ['product_planning', 'project_planning', 'technical_planning'].includes(
            String(chunk.workflow.summary?.phase || '')
          ) &&
          chunk.workflow.summary?.status === 'running'
        if (!hasContent && !requiresInput && !isPlanningRunning) {
          return
        }
        // 新一轮：新增消息卡片，保留历史；清除新轮标志。
        planningNewRoundRef.current = false
        setSessionMessagesRef.current(sessionKey, (prev) => [
          ...prev,
          {
            id: messageId,
            role: 'assistant',
            content: chunk.content || '',
            workflow: chunk.workflow,
            createdAt: messageId
          }
        ])
      }
    } else if (chunk.content !== undefined && chunk.content.trim()) {
      // 纯 content 流式（非空）：同一轮内更新最后一条 assistant 消息，否则新增。
      // UI 确认阶段的单页动作（runId 变化）也更新同一条卡片。
      // 规划阶段流式 token 到达时，最后一条消息可能已是规划 workflow 卡片，
      // 此时合并 content 而不是新增重复消息。
      // 到该卡片，而非新增重复卡片。
      // 用户刚提交后（planningNewRoundRef=true）追加的 assistant 占位消息（无 workflow）
      // 也应被流式 content 更新，而非新增重复卡片。
      const lastIsPlanningStage =
        lastMessage?.role === 'assistant' &&
        ['product_planning', 'project_planning', 'technical_planning'].includes(
          String(lastMessage?.workflow?.summary?.phase || '')
        )
      const lastIsPlaceholder =
        planningNewRoundRef.current &&
        lastMessage?.role === 'assistant' &&
        !lastWorkflowRunId
      // 最后一条已是稳定的 workflow 卡片（待确认/已完成）：后续纯 content（如「还有 N 个问题
      // 需要补充」状态摘要）应合并到该卡片而非新建重复消息，渲染时由 effectiveAssistantContent
      // 隐藏（showWorkflowCard && requiresClarification）。
      const lastIsStableWorkflowCard =
        lastMessage?.role === 'assistant' &&
        Boolean(lastWorkflowRunId) &&
        (lastMessage?.workflow?.summary?.status === 'requires_user_input' ||
          lastMessage?.workflow?.summary?.status === 'completed')
      const appendToLast =
        lastMessage?.role === 'assistant' &&
        (lastIsPlaceholder ||
          lastIsStableWorkflowCard ||
          (!planningNewRoundRef.current &&
            (sameUiDesignConfirmation ||
              lastIsPlanningStage ||
              !lastWorkflowRunId ||
              (Boolean(chunkWorkflowRunId) && lastWorkflowRunId === chunkWorkflowRunId))))
      if (appendToLast) {
        planningNewRoundRef.current = false
        setSessionMessagesRef.current(sessionKey, (prev) => {
          const updated = [...prev]
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: chunk.content || '',
            planningLoading: false
          }
          return updated
        })
      } else {
        planningNewRoundRef.current = false
        setSessionMessagesRef.current(sessionKey, (prev) => [
          ...prev,
          {
            id: messageId,
            role: 'assistant',
            content: chunk.content || '',
            createdAt: messageId
          }
        ])
      }
    }
    // 空 content 且无 workflow：丢弃，不创建空白消息。
    // workflow 到达稳定点（待确认/已完成）时延迟落盘，便于切换工作区或重启后恢复历史。
    if (chunk.workflow) {
      const stable =
        chunk.workflow.summary?.status === 'requires_user_input' ||
        chunk.workflow.summary?.status === 'completed'
      if (stable) {
        console.log('[planning-persist] trigger stable sessionKey=', sessionKey.slice(-12), 'status=', chunk.workflow.summary?.status)
        persistPlanningSession(sessionKey)
      }
    }
  }

  // ensurePlanningSession 用 ref 持有，避免 effect 依赖它循环。
  const ensurePlanningSessionRef = useRef(ensurePlanningSession)
  ensurePlanningSessionRef.current = ensurePlanningSession

  useEffect(() => {
    if (!isDesignPhase || !planningThreadId) return
    // 等 session 列表加载完再 ensure，避免 sessionSummaries 为空时找不到已有 session
    // 而创建新 session，导致历史对话丢失。
    if (loadingSessions) return
    let cancelled = false
    console.log('[planning-session] ensure start', planningThreadId, 'isDesignPhase=', isDesignPhase, 'loadingSessions=', loadingSessions)
    void ensurePlanningSessionRef.current(planningThreadId).then((identity) => {
      if (cancelled) return
      console.log('[planning-session] ensure done', identity.key, 'pending=', pendingPlanningChunksRef.current.length)
      planningSessionKeyRef.current = identity.key
      // 回放 sessionKey 就绪前缓存的 chunk。
      const pending = pendingPlanningChunksRef.current
      if (pending.length) {
        pendingPlanningChunksRef.current = []
        for (const chunk of pending) {
          injectPlanningChunk(identity.key, chunk)
        }
      }
      // 初次进入设计阶段：回放完缓存的 chunk 后若 session 仍无消息（纯进度快照不创建卡片），
      // 注入一条产品 Agent 占位消息，对话区显示「正在准备需求确认…」卡片，
      // 后续 requires_user_input 的 workflow chunk 到达后更新为真实内容。
      const currentMsgs = getSessionMessagesRef.current(identity.key)
      console.log('[planning-placeholder] currentMsgs=', currentMsgs.length, 'pending=', pending.length, 'sessionKey=', identity.key.slice(-12))
      if (currentMsgs.length === 0) {
        const placeholderId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
        console.log('[planning-placeholder] injecting placeholder id=', placeholderId)
        setSessionMessagesRef.current(identity.key, () => [
          {
            id: placeholderId,
            role: 'assistant',
            content: '',
            planningLoading: true,
            createdAt: placeholderId
          }
        ])
      }
      // 诊断：确认规划 session 消息数与 activeSession 是否对齐。
      const msgs = getSessionMessagesRef.current(identity.key)
      console.log('[planning-session] msgs after replay=', msgs.length, 'activeSessionId=', activeSessionIdRef.current, 'match=', activeSessionIdRef.current === identity.sessionId)
    })
    return () => {
      cancelled = true
    }
    // 只依赖 isDesignPhase/planningThreadId/loadingSessions，ensurePlanningSession 用 ref 避免循环。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDesignPhase, planningThreadId, loadingSessions])

  useEffect(() => {
    if (!onPlanningStreamReady) return
    // 不依赖 isDesignPhase：工作台刚进入时 lifecycle 尚未加载，isDesignPhase 为 false，
    // 若此时不注册句柄，Modal 最早的流式数据（"正在生成需求文档大纲…"）会被丢弃。
    // 总是注册，chunk 到达时 sessionKey 未就绪则缓存，待 ensurePlanningSession 完成后回放。
    const injectChunk = (chunk: { content?: string; workflow?: WorkflowRunPayload }): void => {
      const sessionKey = planningSessionKeyRef.current
      console.log('[planning-inject] sessionKey=', sessionKey ? 'ready' : 'empty', chunk.content?.slice(0, 40) || chunk.workflow?.summary?.phase)
      if (!sessionKey) {
        pendingPlanningChunksRef.current.push(chunk)
        return
      }
      injectPlanningChunk(sessionKey, chunk)
    }
    onPlanningStreamReady(injectChunk)
    planningStreamInjectRef.current = injectChunk
    return () => {
      planningStreamInjectRef.current = null
      onPlanningStreamReady(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onPlanningStreamReady])

  // 需求文档确认：不再自动跳过，改为展示产物确认行（放弃/确认保存），
  // 用户手动确认后流转到 UI 确认。右侧自动打开需求文档 tab 展示内容。

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
  const displayedPlanExecutionMode = planEnded
    ? 'idle'
    : deriveDisplayedPlanExecutionMode(
        scopedExecution,
        stopping ? 'stopping' : activeWorkflow?.summary.status,
        loading,
        Boolean(applicationLifecycle)
      )
  const canRetryFailedTasks = workflowCanRetryFailedTasks(activeWorkflow, scopedExecution)
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
  const conversationActive = conversationRunning || isConversationWorkflow(latestWorkflowForDisplay)
  const inputModeLocked = isConversationWaitingForInput(latestWorkflowForDisplay)
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
    !detailConfirmationWaitingReview &&
    !freeChatSelected &&
    !isDesignPhase
  const activeSessionUpdatedAt = sessions.find(
    (session) => session.id === activeSessionId
  )?.updatedAt

  /** 切换当前页面或 API 会话的输入模式，不改变目标会话和历史消息。 */
  const handleInputModeChange = (nextMode: ChatInputMode): void => {
    if (inputModeLocked) return
    setInputModes((current) => ({ ...current, [inputModeKey]: nextMode }))
  }

  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    if (activeApiEndpoint) return
    if (detailTargetSelectionRequired) return
    setActiveDetailTarget((currentTarget) => {
      if (currentTarget.type === 'endpoint') return currentTarget
      if (currentTarget.type === 'none') return currentTarget
      const currentPageId = currentTarget.pageId
      if (displayedPlanningPages.length === 0) return currentTarget
      if (displayedPlanningPages.some((page) => page.pageId === currentPageId)) {
        return currentTarget
      }
      const fallbackPageId =
        displayedPlanningPages.find((page) => page.designed)?.pageId ||
        displayedPlanningPages[0]?.pageId ||
        ''
      return fallbackPageId ? { type: 'page', pageId: fallbackPageId } : { type: 'none' }
    })
  }, [activeApiEndpoint, displayedPlanningPages, detailTargetSelectionRequired])

  // 打开历史页面或接口会话时同步目标上下文，避免标题与消息归属不一致。
  useEffect(() => {
    const session = sessions.find((item) => item.id === activeSessionId)
    if (!session) return
    setFreeChatSelected(!session.pageId && !session.apiContractId && !session.endpointId)
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
    const resolvedPageId = resolvePlanningPageId(displayedPlanningPages, sessionPageId)
    if (resolvedPageId) {
      setActiveDetailTarget({ type: 'page', pageId: resolvedPageId })
    }
  }, [activeSessionId, displayedPlanningPages, sessions])

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
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(true)
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleCreateSessionFromList()
  }

  /** 进入自由对话时只恢复最近会话，不隐式创建新会话。 */
  const handleOpenFreeChat = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(true)
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleSelectFreeChat().catch(() => undefined)
  }

  /** 在指定页面下新建独立会话，并立即切换到该页面。 */
  const handleCreatePageSession = async (pageId: string, pageLabel: string): Promise<void> => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(false)
    setInteractingDetailTargetKey(pageDetailTargetKey(pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId })
    await createPageSession(pageId, pageLabel)
  }

  /** 从应用大纲切换页面；没有消息历史时仅展示空白上下文，不提前创建会话。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    setPreviewError('')
    setActiveView('chat')
    setFreeChatSelected(false)
    setInteractingDetailTargetKey(pageDetailTargetKey(page.pageId))
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'page', pageId: page.pageId })
    handleSelectPage(page.pageId).catch(() => undefined)
  }

  // 选中待设计页面时，确保该页面 session 激活并注入一条 detailBlocker assistant 消息，
  // MessageList 渲染为研发 Agent 流内卡片（「尚未进行详细设计」+ 开始按钮）。
  // 点「开始详细设计」后该消息保留在历史里，与后续 workflow 节点串成完整对话链。
  const blockerInjectedRef = useRef('')
  useEffect(() => {
    if (isDesignPhase || activeWorkbenchPhase !== 'development') return
    if (displayedPlanExecutionMode !== 'idle') return
    // 已从首次目标选择器或挡板卡启动详细设计时，不再插入重复挡板消息。
    if (generatingDetailTargetKey) return
    if (!activePageOption) return
    const pageId = activePageOption.pageId
    if (!pageId) return
    // 已设计页面不注入挡板（已有 detail plan，直接进 workflow 历史）。
    if (activePageOption.designed || activePageOption.hasDetailPlan) {
      blockerInjectedRef.current = pageId
      return
    }
    const alreadyInjected = blockerInjectedRef.current === pageId
    blockerInjectedRef.current = pageId
    // 每次选中待设计页面都确保其 session 激活（设 activeSessionId），避免切回时显示空白。
    ensurePageSession(pageId, activePageOption.label)
      .then((identity) => {
        if (alreadyInjected) return
        const existing = getSessionMessages(identity.key)
        if (existing.some((message) => message.detailBlocker)) return
        const blockerMessage: AgentChatMessage = {
          id: Date.now(),
          role: 'assistant',
          content: '',
          detailBlocker: {
            pageId,
            label: activePageOption.label,
            path: activePageOption.path,
            purpose: activePageOption.purpose
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
    generatingDetailTargetKey,
    activePageOption
  ])

  /** 从应用大纲切换 API；页面和 API 目标互斥，因此会清空当前页面选中态。 */
  const handleApiEndpointSelect = (target: ActiveApiEndpointTarget): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(false)
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
    setFreeChatSelected(false)
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

  /** 启动当前页面的详细设计，并在用户点击按钮后立即更新页面状态。 */
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
    setFreeChatSelected(false)
    // 页面实现契约属于技术规划，不代表用户已经开始设计；此处只在按钮动作发生后置为已设计。
    setStartedPageDesignIds((current) => {
      if (current.has(pageId)) return current
      const next = new Set(current)
      next.add(pageId)
      return next
    })
    const targetKey = pageDetailTargetKey(pageId)
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    setActiveDetailTarget({ type: 'page', pageId })
    const started = await handleStartDetailConfirmation(
      pageId,
      pageLabel,
      hasDetailPlan,
      templateParams
    )
    if (started) {
      onPlanningArtifactsRefresh()
    } else {
      setStartedPageDesignIds((current) => {
        if (!current.has(pageId)) return current
        const next = new Set(current)
        next.delete(pageId)
        return next
      })
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
    setFreeChatSelected(false)
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

  /** 从首次目标选择器直接启动所选页面或接口的详细设计 Workflow。 */
  const handleInitialDetailTargetSelect = async (
    targetType: 'page' | 'endpoint',
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string
      endpointId?: string
    }
  ): Promise<void> => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(false)
    await handleStartDetailDesign(targetType, targetId, targetLabel, hasDetailPlan, targetContext)
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
            templateSourcePath: targetContext.templateSourcePath
          }
        : undefined
    )
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    const session = sessions.find((item) => item.id === sessionId)
    setFreeChatSelected(
      Boolean(session && !session.pageId && !session.apiContractId && !session.endpointId)
    )
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
    answers: ClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>
  ): Promise<void> => {
    setGeneratingDetailTargetKey('')
    console.log('[planning-submit] handleSubmitWorkflowClarification isDesignPhase=', isDesignPhase, 'answers=', answers, 'workflow.runId=', workflow.runId, 'workflow.phase=', workflow.summary?.phase)
    // 设计阶段：规划确认走 planningSubmitRef（Modal 的 runPlanning），不走开发 workflow。
    if (isDesignPhase) {
      // UI 设计稿的单页动作（换一换/选模板/调整）是同一轮内的更新，不新增消息卡片，
      // 只更新现有卡片；只有推进到下一阶段（确认全部/需求确认/项目规划）才新增卡片。
      if (!answers || !('ui_design_action' in answers)) {
        planningNewRoundRef.current = true
        // 用户操作留痕：把确认/放弃/填表等操作作为 user 消息追加到对话区。
        appendPlanningUserMessage(answers)
      }
      onSubmitPlanningClarification(workflow, answers, editedRequirementSpec)
      return
    }
    await handleSubmitClarification(workflow, answers)
  }

  // 需求文档确认：保存编辑草稿（重写 Markdown+JSON），不确认也不继续规划。
  // 保存后把更新后的 workflow 注入回规划会话，驱动右侧需求文档 tab 实时刷新
  // 编辑后的内容（confirmationArtifact.content 与 state.requirement_spec 同步更新）。
  const handleSaveRequirementSpec = useCallback(
    async (
      workflow: WorkflowRunPayload,
      spec: Record<string, unknown>
    ): Promise<Record<string, unknown> | undefined> => {
      const workspaceRoot = application.workspaceRoot || ''
      const threadId = workflow.threadId || planningThreadId || ''
      if (!workspaceRoot) return undefined
      try {
        const saved = await saveRequirementSpecDraft(workspaceRoot, spec, threadId)
        message.success('需求文档修改已同步到 Markdown')
        // 把保存后的 artifact 与 spec 注入回规划会话，更新当前需求确认卡片与右侧文档。
        const inject = planningStreamInjectRef.current
        if (inject) {
          inject({
            workflow: {
              ...workflow,
              confirmationArtifact: saved.artifact,
              state: { ...workflow.state, requirement_spec: saved.requirementSpec },
              result: { ...workflow.result, requirement_spec: saved.requirementSpec }
            }
          })
        }
        return saved.requirementSpec
      } catch (reason) {
        if (isAuthenticationFailure(reason)) return undefined
        message.error(formatError(reason, '保存需求文档失败'))
        return undefined
      }
    },
    [application.workspaceRoot, planningThreadId]
  )

  // 设计阶段 ChatComposer 提交：把用户输入作为需求意见提交给规划 workflow。
  // 当前 planningWorkflow 的 clarification.mode 决定答案键；无明确 mode 时作为
  // requirement_spec_confirmation 提交，后端按需求确认意见处理。
  const handleDesignPhaseSend = async (): Promise<void> => {
    const trimmed = draft.trim()
    if (!trimmed || !planningWorkflow) return
    const clarification = planningWorkflow.summary?.clarification as
      | { mode?: string }
      | undefined
    const mode = clarification?.mode
    const answers: WorkflowClarificationAnswers =
      mode === 'product_plan_confirmation'
        ? { product_plan_confirmation: trimmed }
        : mode === 'technical_plan_confirmation'
          ? { technical_plan_confirmation: trimmed }
          : mode === 'project_plan_confirmation'
            ? { project_plan_confirmation: trimmed }
            : mode === 'ui_design_confirmation'
              ? { ui_design_confirmation: trimmed }
              : { requirement_spec_confirmation: trimmed }
    // 标记新一轮：下一次 workflow chunk 到达时新增消息卡片，保留本轮对话历史。
    planningNewRoundRef.current = true
    // 用户输入留痕：把输入文本作为 user 消息追加到对话区。
    appendPlanningUserMessage(answers)
    onSubmitPlanningClarification(planningWorkflow, answers)
    // 提交后清空 draft（与 handleSend 行为一致）。
    setDraftByKey(draftKey, '')
  }

  /** 滚动到现有 Workflow 进度区域，不改变消息列表和中央内容结构。 */
  const handleViewPlan = (): void => {
    document.querySelector(`.${CLASS_PREFIX}-process-steps`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center'
    })
  }

  /** 用户点击"进入开发阶段"：放开 product 锁，恢复跟随旅程（derivedPhase=development）。
   *  同时清空 activeSessionId：设计阶段绑定的是 planning session，进入开发后清掉其历史，
   *  对话区留空，等用户点页面/API 再展示研发 Agent 卡片。 */
  const handleEnterDevelopment = useCallback((): void => {
    window.localStorage.setItem(enterDevConfirmedKey, '1')
    setEnterDevConfirmed(true)
    clearActiveSession()
    setActiveDetailTarget({ type: 'none' })
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setFreeChatSelected(false)
    setRightPanel(undefined)
    setActiveView('chat')
    switchPhase(null)
  }, [enterDevConfirmedKey, switchPhase, clearActiveSession])

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
        showRightPanel && 'embedded-preview-open',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        elementInspectionActive && 'element-inspection-active',
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
          forceCollapsed={isDesignPhase}
          freeChatActive={
            freeChatSelected && activeView === 'chat' && activeDetailTarget.type === 'none'
          }
          loadingSessions={loadingSessions}
          outlineLocked={detailTargetSelectionRequired}
          onCreateFreeChatSession={handleCreateChatSession}
          onCreatePageSession={handleCreatePageSession}
          onCreateEndpointSession={handleCreateEndpointSession}
          onDeleteSession={handleDeleteSession}
          onApiEndpointSelect={handleApiEndpointSelect}
          onOpenFreeChat={handleOpenFreeChat}
          onOpenSession={handleOpenChatSession}
          onPageSelect={handlePageSelect}
          onReturnWelcome={onReturnWelcome}
          onShowFiles={handleShowFiles}
          onShowSettings={handleShowSettings}
          onShowSkills={handleShowSkills}
          onThemeChange={onThemeChange}
          pages={displayedPlanningPages}
          pageTree={displayedPlanningPageTree}
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
        ) : detailTargetSelectionRequired ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              apiContracts={developmentPlanningApiContracts}
              disabled={loading || workspaceBusy}
              generating={loading}
              loading={!developmentPlanningReady}
              onStart={handleInitialDetailTargetSelect}
              pages={displayedPlanningPages}
              pageTree={displayedPlanningPageTree}
              selectedEndpoint={activeApiEndpoint}
              workflowEvents={activeWorkflow?.events}
            />
          </div>
        ) : (
          <div className={cx('ai-chat-main')}>
            {activeDetailTarget.type !== 'none' ? (
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
                previewLaunchLoading={previewLaunchLoading}
                status={activeHeaderStatus}
                targetType={activeHeaderTarget.type}
                theme={theme}
              />
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
              applicationLifecycle={applicationLifecycle}
              codeChangeActionsDisabled={loading || workspaceBusy}
              conversationRunning={conversationRunning}
              designPhasePlanning={isDesignPhase}
              key={activeSession?.key || draftKey}
              loading={loading}
              messages={messages}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onRevertCodeChanges={requestCodeChangeRevert}
              onSubmitClarification={handleSubmitWorkflowClarification}
              revertingCodeChangeIds={revertingCodeChangeIds}
              uiDesignActivePageId={uiDesignActivePageId}
              onUiDesignActivePageChange={handleUiDesignActivePageChange}
              uiDesignActionPageId={uiDesignActionPageId}
              onUiDesignActionPageIdChange={setUiDesignActionPageId}
              onSaveRequirementSpec={handleSaveRequirementSpec}
              rootPath={application.schema?.menus?.rootPath || '/'}
              onEnterDevelopment={handleEnterDevelopment}
              onRetryTemplate={onRetryTemplate}
              generatingTemplate={generatingTemplate}
              onStartDetailDesign={(page) => {
                void handleStartDetailDesign(
                  'page',
                  page.pageId,
                  page.label,
                  Boolean(page.hasDetailPlan)
                )
              }}
            />

            {detailProgressVisible && activePageOption ? (
              <div className={cx('detail-progress-card')}>
                <div className={cx('ai-message-agent', 'development')}>
                  <span className={cx('ai-message-agent-avatar')} aria-hidden="true">
                    <UserOutlined />
                  </span>
                  <span className={cx('ai-message-agent-name')}>研发 Agent</span>
                </div>
                <PageDesignProgress
                  events={activeWorkflow?.events}
                  pageLabel={activePageOption.label || activePageOption.pageId}
                  targetType="page"
                />
              </div>
            ) : null}

            {shouldRenderPlanExecutionDock(displayedPlanExecutionMode, conversationActive) ? (
              <WorkspaceDebugDock
                activeWorkflow={activeWorkflow}
                copy={copy}
                initialResumeFrom={workflowResumeNode(activeWorkflow, scopedExecution?.phase)}
                loading={loading}
                onSend={
                  planExecutionShowsDebugResume(displayedPlanExecutionMode) && activeWorkflow
                    ? handleResumePlan
                    : handleSend
                }
                onStopGenerating={handleStopGenerating}
                rightContent={
                  <PlanExecutionDock
                    canRetryFailedTasks={canRetryFailedTasks}
                    dependencyLocked={targetExecutionContext.dependencyLocked}
                    error={scopedExecution?.error?.message || error}
                    execution={scopedExecution}
                    mode={displayedPlanExecutionMode}
                    onAccept={handleAcceptPreview}
                    onAdjust={handleAdjustPlan}
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
                }
                stopping={stopping}
                workspaceBusy={workspaceBusy}
                workspaceRoot={workspaceRoot}
              />
            ) : (
              <>
                <ChatComposer
                  activeWorkflow={activeWorkflow}
                  copy={copy}
                  draft={draft}
                  error={error}
                  inputMode={inputMode}
                  inputModeDisabled={inputModeLocked}
                  loading={loading}
                  onDraftChange={(value) => setDraftByKey(draftKey, value)}
                  onInputModeChange={
                    activeDetailTarget.type === 'none' ? undefined : handleInputModeChange
                  }
                  onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                  onSend={isDesignPhase ? handleDesignPhaseSend : handleSend}
                  onStopGenerating={handleStopGenerating}
                  stopping={stopping}
                  selectedSkills={selectedSkills}
                  workspaceBusy={workspaceBusy}
                  workspaceRoot={workspaceRoot}
                />
                {displayedPlanExecutionMode !== 'idle' && !conversationActive ? (
                  <WorkspaceDebugDock
                    activeWorkflow={activeWorkflow}
                    copy={copy}
                    initialResumeFrom={workflowResumeNode(activeWorkflow, scopedExecution?.phase)}
                    loading={loading}
                    onSend={handleSend}
                    onStopGenerating={handleStopGenerating}
                    stopping={stopping}
                    workspaceBusy={workspaceBusy}
                    workspaceRoot={workspaceRoot}
                  />
                ) : null}
              </>
            )}

            {/* page 待设计走对话区 detailBlocker 流内卡片；endpoint 待设计仍用 locked 选择器。 */}
            {requiresEndpointDetailDesign(activeApiEndpointOption?.endpoint) &&
            displayedPlanExecutionMode === 'idle' &&
            !detailConfirmationWaitingReview ? (
              <DetailConfirmationPageSelector
                disabled={loading || workspaceBusy}
                generating={false}
                loading={false}
                mode="locked"
                onStart={handleStartDetailDesign}
                pages={displayedPlanningPages}
                selectedEndpoint={activeEndpointSelectorTarget}
                selectedPage={activePageOption}
                workflowEvents={activeWorkflow?.events}
              />
            ) : null}
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
          aria-valuenow={Math.round(assistantPanelRatio * 100)}
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

      {showRightPanel && rightPanel?.type === 'doc' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={workspaceTabs}
            active={activeWorkspaceTab}
            onChange={openWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            {isDesignPhase && activeDesignDocKey === 'ui-design' ? (
              <UiDesignPreviewPanel
                activePageId={uiDesignActivePage?.pageId || ''}
                code={uiDesignActivePageCode}
                generating={uiDesignGenerating}
                actionPageId={uiDesignActionPageId}
                onPageChange={setUiDesignActivePageId}
                pages={uiDesignPages}
              />
            ) : isDesignPhase ? (
              <DocPanel
                content={designDocContent}
                docName={designDocName}
                generating={designDocGenerating}
                title={designDocTitle}
              />
            ) : (
              <DocPanel
                content={designDocContent}
                docName={designDocName}
                generating={designDocGenerating}
                title={designDocTitle}
              />
            )}
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'preview' && (
        <div className={cx('embedded-preview-pane')}>
          <RightPanelTabs
            tabs={workspaceTabs}
            active={activeWorkspaceTab}
            onChange={openWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <BrowserPreviewPanel
            application={application}
            pages={displayedPlanningPages}
            requestKey={rightPanel.requestKey}
            requestedUrl={rightPanel.url}
            previewBaseUrl={runtimePreviewBaseUrl}
            selectedPagePath={activeHeaderTarget.type === 'page' ? activeHeaderTarget.path : '/'}
            errorMessage={runtimePreviewLaunchError}
            onInspectingChange={setElementInspectionActive}
          />
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'source' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={workspaceTabs}
            active={activeWorkspaceTab}
            onChange={openWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            <SourcePanel
              workspaceRoot={application.workspaceRoot || ''}
              initialFilePath={
                activePageOption
                  ? `frontend/src/pages/${activePageOption.key || activePageOption.pageId}/index.tsx`
                  : undefined
              }
            />
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'diff' && (
        <div className={cx('embedded-preview-pane', 'diff-detail-pane')}>
          <CodeDiffDetailPanel
            codeChanges={rightPanel.codeChanges}
            selectedPath={rightPanel.selectedPath}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
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

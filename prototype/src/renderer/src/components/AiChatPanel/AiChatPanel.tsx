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
  DevelopmentPlanningEntity,
  EditorMode,
  WorkflowRunPayload
} from '../../typings'
import { composePreviewUrl, cx, previewOrigin } from '../../utils'
import type { ChatSessionSummary } from '../../service/chatSessions'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeChangeCard from './components/CodeChangeCard'
import MessageList from './components/MessageList'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import SourcePanel from './components/SourcePanel'
import {
  buildEndpointSource,
  buildPageSource,
  buildProjectPlanDoc,
  buildRequirementSpecDoc,
  type PageDesign,
  type TestReportSnapshot
} from '../../workbenchArtifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import {
  APPLICATION_ROOT,
  WORKSPACE_DOC_PATHS,
  appPath,
  workspaceScaffoldDirectories,
  workspaceScaffoldFiles
} from '../../mock/workspaceFiles'
import {
  compareWorkbenchPhases,
  isInitialPlanningPhase,
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../../workbenchPhase'
import {
  artifactIdsForSession,
  documentArtifactId,
  entityArtifactId,
  endpointArtifactId,
  pageArtifactId,
  resolveArtifactAccess,
  resolveArtifactOwners,
  type WorkbenchArtifact,
  type WorkbenchArtifactAccess,
  type WorkbenchArtifactStatus
} from '../../workbenchDomain'
import {
  type WorkspaceTab,
  type WorkspaceTabKey
} from './components/RightPanelTabs'
import WorkbenchRightPanel from './components/WorkbenchRightPanel'
import type { RightPanelLayout } from './types'
import SessionSidebar from './components/SessionSidebar'
import DevelopmentConversationModal, {
  DevelopmentArtifactConversationConfirmModal,
  DevelopmentStageCompleteModal,
  TestingStageCompleteModal,
  type DevelopmentConversationTarget,
  type DevelopmentConversationTreeNode
} from './components/DevelopmentConversationModal'
import PageContextHeader, { type ConversationArtifact } from './components/PageContextHeader'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import type { RelatedEndpointContext } from './hooks/useChatSessions'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { chatCopy } from './constants'
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  sessionDetailTargetKey,
  workflowCodeChanges,
  type WorkflowPreviewTarget
} from './utils'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentPlanningReady: boolean
  hasPageDesigns: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningPageTree: DevelopmentPlanningPageTreeNode[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  developmentPlanningEntities: DevelopmentPlanningEntity[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  previewBaseUrl: string
  previewLaunchError: string
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
  /** 顶部阶段条请求打开“进入测试”确认弹框的自增信号。 */
  testingEntryRequest?: number
  /** 向顶部阶段条上报测试阶段是否具备进入条件（“允许进入”不等于“已进入”）。 */
  onTestingEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求打开“进入审查”确认弹框的自增信号。 */
  reviewEntryRequest?: number
  /** 向顶部阶段条上报测试通过后是否具备进入审查条件。 */
  onReviewEntryAvailableChange?: (available: boolean) => void
}
type ActiveView = 'chat' | 'skills' | 'files' | 'settings'
type SidebarDocumentKey = WorkspaceDocKey | 'test-report' | 'code-review'

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

type DevelopmentAutoTarget =
  | { kind: 'page'; artifactId: string; page: DevelopmentPlanningPageOption }
  | {
      kind: 'endpoint'
      artifactId: string
      apiContractId: string
      description: string
      endpointId: string
      endpointLabel: string
      path: string
    }

type DevelopmentCompletionCandidate = {
  runId: string
  artifactId: string
}

type ProjectDocumentConfig = {
  content: string
  onSaveEdit?: (draft: string) => void
  readOnly: boolean
}

type DetailBlockerMessage = NonNullable<AgentChatMessage['detailBlocker']>

/** 返回当前阶段最近使用的应用级会话；页面和接口会话只归开发阶段管理。 */
function latestStageSession(
  sessions: ChatSessionSummary[],
  phase: WorkbenchPhase
): ChatSessionSummary | undefined {
  return sessions
    .filter(
      (session) =>
        session.sessionKind === phase && !session.pageId && !session.apiContractId && !session.endpointId
    )
    .sort((left, right) => right.updatedAt - left.updatedAt)[0]
}

/** 从生命周期扩展读取当前测试报告，报告正文和产物状态共享同一份快照。 */
function readTestReportSnapshot(
  extensions: Record<string, unknown>
): TestReportSnapshot | undefined {
  const rawStatus = String(extensions.testReportStatus || '')
  if (!['running', 'failed', 'passed'].includes(rawStatus)) return undefined
  const round = Number(extensions.testReportRound || 0)
  if (!Number.isFinite(round) || round <= 0) return undefined
  return {
    round,
    status: rawStatus as TestReportSnapshot['status'],
    basedOnRevision: Number(extensions.testReportBasedOnRevision || 1),
    defects: Array.isArray(extensions.testReportDefects)
      ? (extensions.testReportDefects as TestReportSnapshot['defects'])
      : []
  }
}

/** 为确认进入测试阶段创建瞬时应用级执行快照，让阶段位置先于测试 Agent 加载切换。 */
function beginTestingExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-testing-entry-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: lifecycle.revision + 1,
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'application_test',
        status: 'running',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 为确认进入审查阶段创建瞬时应用级执行快照，让审查 Agent 在确认后再启动。 */
function beginReviewExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-review-entry-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: lifecycle.revision + 1,
    extensions: {
      ...lifecycle.extensions,
      reviewEntryConfirmed: true
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'code_review',
        status: 'running',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 将已确认的审查报告收口为审查通过态，随后由阶段推导自动进入验收。 */
function completeReviewExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-review-complete-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: lifecycle.revision + 1,
    extensions: {
      ...lifecycle.extensions,
      reviewEntryConfirmed: true,
      reviewStatus: 'passed'
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'code_review',
        status: 'completed',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 记录用户对当前预览交付的明确验收确认，不改变验收对话归属。 */
function completeAcceptanceExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-acceptance-complete-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: lifecycle.revision + 1,
    extensions: {
      ...lifecycle.extensions,
      acceptanceStatus: 'passed',
      acceptanceBasedOnRevision: lifecycle.revision
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'acceptance',
        status: 'completed',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

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

/** 从原型的单文件新增 Diff 还原待保存文本；非新增行保留现有文件内容作为安全兜底。 */
function contentFromFileDiff(diff: string, currentContent = ''): string {
  const addedLines = diff
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
  return addedLines.length > 0 ? addedLines.join('\n') : currentContent
}


/**
 * 设计阶段右栏文档 tab 跟随规则：回看时优先按当前查看阶段定位，
 * 正常推进时生成中按 workflow.phase、就绪后按 lifecycle.stage 接管切到内容。
 */
function resolveDesignDocKey(
  stage: string | undefined,
  phase: string | undefined,
  phaseRunning: boolean,
  viewingPhase?: WorkbenchPhase
): WorkspaceDocKey | undefined {
  // 回看设计阶段时，左侧产物必须跟随当前查看阶段，不能被生命周期已到达的最新文档覆盖。
  if (viewingPhase === 'analysis') return 'requirement-spec'
  if (viewingPhase === 'planning') return 'project-plan'
  const generatingKey =
    phase === 'requirements' && phaseRunning
      ? 'requirement-spec'
      : phase === 'project_planning' && phaseRunning
        ? 'project-plan'
        : undefined
  return generatingKey ?? designActiveDocKey(stage)
}

/** 判断设计阶段的文件改动是否仍属于当前阶段的待授权生成，过滤已确认后残留的历史快照。 */
function isPendingDesignCodeChange(
  workflow: WorkflowRunPayload | undefined,
  stage: string | undefined
): boolean {
  const phase = String(workflow?.summary?.phase || '')
  const running = workflow?.summary?.status === 'running'
  if (phase === 'requirements') {
    // 需求生成期间即使生命周期仍停留在分析阶段，也要持续展示每一帧 Diff。
    return (
      (running ||
        stage === 'generating_requirement_spec' ||
        stage === 'awaiting_requirement_confirmation')
    )
  }
  if (phase === 'project_planning') {
    // 项目 Agent 的首个 running 快照到来时，生命周期通常还没切到计划阶段；
    // 不能因此过滤掉渐进写入的中间快照，否则用户只能看到最后一帧。
    return (
      (running ||
        stage === 'generating_project_plan' ||
        stage === 'awaiting_project_plan_confirmation')
    )
  }
  // 页面/接口构建与测试/审查报告沿用各自工作流的当前 Diff，不按查看阶段过滤。
  return true
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
  developmentPlanningEntities,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey,
  testingEntryRequest,
  onTestingEntryAvailableChange,
  onReviewEntryAvailableChange,
  reviewEntryRequest
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  // 设计阶段文档编辑态:editedDesignDocs 存快捷键保存后的编辑版(覆盖静态产物显示);
  // 编辑草稿由 DocPanel 内部管理(默认即编辑,IDE 式),Ctrl/Cmd+S 后经 onSaveEdit(draft) 回传。
  const [editedDesignDocs, setEditedDesignDocs] = useState<
    Partial<Record<WorkspaceDocKey, string>>
  >({})
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  const [developmentConversationModalOpen, setDevelopmentConversationModalOpen] = useState(false)
  const [pendingDevelopmentConversationTarget, setPendingDevelopmentConversationTarget] =
    useState<DevelopmentConversationTarget>()
  const [developmentCompleteModalOpen, setDevelopmentCompleteModalOpen] = useState(false)
  const [testingTransitionRequested, setTestingTransitionRequested] = useState(false)
  const [reviewCompleteModalOpen, setReviewCompleteModalOpen] = useState(false)
  const [reviewTransitionRequested, setReviewTransitionRequested] = useState(false)
  // 开发门禁状态按版本保留：阶段准入必须消费已确认的产物状态，而不是瞬时设计标记。
  const [developmentStatusState, setDevelopmentStatusState] = useState<{
    statuses: Record<string, WorkbenchArtifactStatus>
    versionKey: string
  }>(() => ({ statuses: {}, versionKey: versionViewKey }))
  const [developmentCompletionCandidate, setDevelopmentCompletionCandidate] = useState<
    DevelopmentCompletionCandidate | undefined
  >()
  const activeDetailTargetRef = useRef<ActiveDetailTarget>(activeDetailTarget)
  const [, setInteractingDetailTargetKey] = useState('')
  const [, setGeneratingDetailTargetKey] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [elementInspectionActive, setElementInspectionActive] = useState(false)
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  const { publishAiMessage } = useWorkbench()
  // 阶段门禁：查看阶段决定当前 Agent 与编辑权限，执行阶段和已到达阶段独立保留。
  const {
    reachedPhase,
    viewingPhase: activeWorkbenchPhase
  } = useWorkbenchPhase()
  const acceptanceAccepted =
    String(applicationLifecycle?.extensions?.acceptanceStatus || '') === 'passed'
  const [viewingTaskPhase, setViewingTaskPhase] = useState<WorkbenchPhase>(activeWorkbenchPhase)
  useEffect(() => {
    // 当前执行进入设计、测试、审查或验收阶段时立即同步查看位置，避免沿用上一阶段内容。
    if (
      activeWorkbenchPhase === 'analysis' ||
      activeWorkbenchPhase === 'planning' ||
      activeWorkbenchPhase === 'testing' ||
      activeWorkbenchPhase === 'review' ||
      activeWorkbenchPhase === 'acceptance'
    ) {
      setViewingTaskPhase(activeWorkbenchPhase)
    }
  }, [activeWorkbenchPhase])
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
  const developmentConversationTree = useMemo<DevelopmentConversationTreeNode>(() => {
    const pageTargets = new Map(
      developmentPlanningPages.map((page) => {
        const relatedEndpoint = resolvePageRelatedEndpoint(
          page.pageId,
          pageDesignCatalog,
          developmentPlanningApiContracts
        )
        const target: DevelopmentConversationTarget = {
          description: page.purpose,
          id: `page:${page.pageId}`,
          kind: 'page' as const,
          label: page.label,
          pageId: page.pageId,
          path: page.path,
          relatedArtifactLabel: relatedEndpoint?.endpointLabel
        }
        return [page.pageId, target] as const
      })
    )

    /** 保留项目计划中的菜单层级，并把可开发页面挂到对应叶子节点。 */
    const mapPageNode = (
      node: DevelopmentPlanningPageTreeNode
    ): DevelopmentConversationTreeNode | undefined => {
      if (node.type === 'menu') {
        const children = (node.children || [])
          .map(mapPageNode)
          .filter((child): child is DevelopmentConversationTreeNode => Boolean(child))
        if (children.length === 0) return undefined
        return {
          children,
          id: `page-group:${node.key}`,
          kind: 'group',
          label: node.label
        }
      }
      const target = pageTargets.get(node.pageId || node.key)
      return target
        ? {
            id: target.id,
            kind: 'page',
            label: target.label,
            target
          }
        : undefined
    }

    const pageNodes = developmentPlanningPageTree
      .map(mapPageNode)
      .filter((node): node is DevelopmentConversationTreeNode => Boolean(node))
    const fallbackPageNodes = [...pageTargets.values()].map((target) => ({
      id: target.id,
      kind: 'page' as const,
      label: target.label,
      target
    }))
    const endpointGroups = developmentPlanningApiContracts
      .map((contract) => {
        const children = contract.endpoints.map((endpoint, endpointIndex) => {
          const apiContractId = endpoint.apiContractId || contract.id
          const endpointId = endpoint.id || String(endpointIndex + 1)
          const target: DevelopmentConversationTarget = {
            apiContractId,
            description: endpoint.summary,
            endpointId,
            id: `endpoint:${apiContractId}:${endpointId}`,
            kind: 'endpoint' as const,
            label: `${endpoint.method} ${endpoint.path}`,
            path: endpoint.path
          }
          return {
            id: target.id,
            kind: 'endpoint' as const,
            label: target.label,
            target
          }
        })
        return {
          children,
          id: `endpoint-group:${contract.id}`,
          kind: 'group' as const,
          label: contract.label
        }
      })
      .filter((group) => group.children.length > 0)

    const rootGroups: DevelopmentConversationTreeNode[] = []
    if (pageNodes.length > 0 || fallbackPageNodes.length > 0) {
      rootGroups.push({
        children: pageNodes.length > 0 ? pageNodes : fallbackPageNodes,
        id: 'development-pages',
        kind: 'group',
        label: '页面'
      })
    }
    if (endpointGroups.length > 0) {
      rootGroups.push({
        children: endpointGroups,
        id: 'development-endpoints',
        kind: 'group',
        label: '接口'
      })
    }
    return {
      children: rootGroups,
      id: 'development-application',
      kind: 'application',
      label: application.name
    }
  }, [
    application.name,
    developmentPlanningApiContracts,
    developmentPlanningPageTree,
    developmentPlanningPages,
    pageDesignCatalog
  ])
  const developmentConversationTargetCount = useMemo(() => {
    /** 递归统计可被选中的页面与接口叶子。 */
    const countTargets = (node: DevelopmentConversationTreeNode): number =>
      (node.target ? 1 : 0) +
      (node.children || []).reduce((sum, child) => sum + countTargets(child), 0)
    return countTargets(developmentConversationTree)
  }, [developmentConversationTree])
  // 分析和计划阶段共用现有规划工作区；后续任务再拆分两套对话流程。
  const isDesignPhase = activeWorkbenchPhase === 'analysis' || activeWorkbenchPhase === 'planning'
  // 设计阶段由当前工作台阶段直接决定 Agent 身份；会话切换完成前不沿用上一阶段的消息和加载态。
  const renderedTaskPhase = isDesignPhase ? activeWorkbenchPhase : viewingTaskPhase
  const displayIsDesignPhase =
    renderedTaskPhase === 'analysis' || renderedTaskPhase === 'planning'
  const displayIsTestingPhase = renderedTaskPhase === 'testing'
  const displayIsReviewPhase = renderedTaskPhase === 'review'
  const displayIsAcceptancePhase = renderedTaskPhase === 'acceptance'
  const viewingHistoricalStage = renderedTaskPhase !== activeWorkbenchPhase
  // 所有模块(页面+接口)开发完成后仅提示用户确认，由用户决定是否进入测试阶段。
  // 准入必须读取当前工作台动态产物清单，不能读取静态演示剧本，否则未开始页面会被误判为已完成。
  const planningPages = developmentPlanningPages
  const planningContracts = developmentPlanningApiContracts
  const testReportExtensions = (applicationLifecycle?.extensions || {}) as Record<string, unknown>
  const testReportSnapshot = readTestReportSnapshot(testReportExtensions)
  const testReportPassedForEntry = testReportSnapshot?.status === 'passed'
  const reviewCompletionPromptRef = useRef('')
  const allDevelopmentModulesComplete =
    planningPages.length > 0 &&
    planningPages.every((page) => {
      const pageId = String(page.pageId || '')
      return (
        (developmentStatusState.versionKey === versionViewKey &&
          developmentStatusState.statuses[pageArtifactId(pageId)] === 'completed')
      )
    }) &&
    planningContracts.every((contract) =>
      contract.endpoints.every((endpoint) =>
        (developmentStatusState.versionKey === versionViewKey &&
          developmentStatusState.statuses[
            endpointArtifactId(contract.id, String(endpoint.id || ''))
          ] === 'completed')
      )
    )
  const {
    assistantPanelWidth,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    rightPanelLayout,
    setRightPanelLayout,
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
      setRightPanelLayout('split')
    },
    [setRightPanelLayout, versionViewKey]
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
    attachArtifactsToActiveSession,
    createPageSession,
    createReviewSession,
    createTestingSession,
    createAcceptanceSession,
    ensureAnalysisSession,
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
    handleRenameSession,
    handleSelectEndpoint,
    handleSelectPage,
    loadingSessions,
    ensurePlanningSession,
    messages,
    persistSession,
    recordAcceptedFile,
    runningSessionsRef,
    selectedSkills,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({ application, editorMode })
  /** 以已授权保存的正式源码判断开发产物是否交付，不使用详设计划状态抢先完成。 */
  const hasSavedCodeFile = (suffix: string): boolean =>
    sessions.some((session) =>
      (session.savedFiles || []).some((file) => {
        const path = file.path.replace(/\\/g, '/').replace(/^.*?wh-branch-pms-new\//, '')
        return path === suffix || path.endsWith(`/${suffix}`)
      })
    )
  const isPageCodeDelivered = (pageId: string): boolean =>
    hasSavedCodeFile(`frontend/pages/${pageId}.tsx`)
  const isEndpointCodeDelivered = (endpointId: string): boolean =>
    endpointId === 'ep-my-rechecks' && hasSavedCodeFile('backend/rechecks-controller.java')
  const activePageCodeDelivered = Boolean(
    activePageOption && isPageCodeDelivered(activePageOption.pageId)
  )
  const directModificationEnabled = activeApiEndpoint
    ? isEndpointCodeDelivered(activeApiEndpoint.endpointId)
    : Boolean(activePageOption && isPageCodeDelivered(activePageOption.pageId))
  const conversationPhase = useMemo<WorkbenchPhase>(() => {
    const sessionKind = activeSession?.sessionKind
    if (
      sessionKind === 'analysis' ||
      sessionKind === 'planning' ||
      sessionKind === 'development' ||
      sessionKind === 'testing' ||
      sessionKind === 'review' ||
      sessionKind === 'acceptance'
    ) {
      return sessionKind
    }
    if (activeSession?.pageId || activeSession?.endpointId) return 'development'
    const sessionTitle = sessions.find((session) => session.id === activeSessionId)?.title || ''
    if (sessionTitle.includes('应用测试')) return 'testing'
    if (sessionTitle.includes('代码审查')) return 'review'
    if (sessionTitle.includes('应用验收')) return 'acceptance'
    if (sessionTitle === '项目计划') return 'planning'
    if (sessionTitle.includes('需求分析')) return 'analysis'
    return renderedTaskPhase
  }, [
    activeSession?.endpointId,
    activeSession?.pageId,
    activeSession?.sessionKind,
    activeSessionId,
    renderedTaskPhase,
    sessions
  ])
  const expectedDesignSessionKind = activeWorkbenchPhase === 'planning' ? 'planning' : 'analysis'
  const designSessionSwitching =
    isDesignPhase &&
    activeSession?.sessionKind !== expectedDesignSessionKind

  // 分析阶段默认会话只归属需求文档，计划阶段默认会话只归属项目计划。
  const formalAnalysisSession = useMemo(() => {
    const candidates = sessions.filter(
      (session) =>
        !session.pageId &&
        !session.endpointId &&
        (session.sessionKind === 'analysis' || (session.title || '').includes('需求分析'))
    )
    return candidates.sort((a, b) => a.createdAt - b.createdAt)[0]
  }, [sessions])
  const formalPlanningSession = useMemo(
    () =>
      sessions
        .filter(
          (session) =>
            !session.pageId &&
            !session.endpointId &&
            (session.sessionKind === 'planning' || session.title === '项目计划')
        )
        .sort((a, b) => a.createdAt - b.createdAt)[0],
    [sessions]
  )

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
    ensureAnalysisSession,
    ensurePlanningSession,
    ensureReviewSession: createReviewSession,
    ensureTestingSession: createTestingSession,
    ensureAcceptanceSession: createAcceptanceSession,
    ensureEndpointSession,
    ensurePageSession,
    attachArtifactsToActiveSession,
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
    planningPhase: activeWorkbenchPhase === 'planning',
    testingPhase: activeWorkbenchPhase === 'testing',
    autoStartDesign: isInitialPlanningPhase(applicationLifecycle),
    autoStartTesting:
      allDevelopmentModulesComplete && testingTransitionRequested,
    acceptancePhase: displayIsAcceptancePhase,
    // 合格测试报告将最高到达阶段推进为审查；进入审查后由审查 Agent 自动开启默认对话。
    autoStartReview:
      !versionReadOnly &&
      activeWorkbenchPhase === 'review' &&
      testReportPassedForEntry,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  })

  const applicationPreviewUrl = composeVersionPreviewUrl(runtimePreviewBaseUrl, '/', versionViewKey)
  const acceptancePreviewEntryRef = useRef('')
  useEffect(() => {
    // 验收进入时默认全宽展示独立“应用预览”Tab；后续 Tab 切换不再改变用户选择的宽度。
    if (!displayIsAcceptancePhase || !applicationPreviewUrl) {
      if (!displayIsAcceptancePhase) {
        acceptancePreviewEntryRef.current = ''
      }
      return
    }
    const entryKey = `${versionViewKey}:${String(applicationLifecycle?.extensions?.reviewStatus || '')}`
    if (acceptancePreviewEntryRef.current === entryKey) return
    acceptancePreviewEntryRef.current = entryKey
    setRightPanel({
      type: 'preview',
      requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:acceptance`,
      url: applicationPreviewUrl
    })
    setRightPanelLayout('full')
  }, [
    applicationLifecycle?.extensions?.reviewStatus,
    applicationPreviewUrl,
    displayIsAcceptancePhase,
    runtimePreviewBaseUrl,
    setRightPanel,
    setRightPanelLayout,
    versionViewKey
  ])

  /** 切换右侧工作区三档布局：隐藏、分栏和全宽覆盖。 */
  const handleRightPanelLayoutChange = (layout: RightPanelLayout): void => {
    if (layout === 'hidden') {
      // 隐藏只收起工作区，不清除当前 Tab，恢复后仍回到用户上一次查看的内容。
      autoOpenStateRef.current.dismissed = true
      setRightPanelLayout('hidden')
      return
    }
    if (layout === 'split') {
      // 极早期尚未生成右侧内容时，先提供稳定的文档工作区，避免“分栏”后为空。
      if (!rightPanel) setRightPanel({ type: 'doc' })
      setRightPanelLayout('split')
      return
    }
    // 全宽仅改变公共面板尺寸；当前文件、浏览器或应用预览 Tab 保持不变。
    if (!rightPanel) setRightPanel({ type: 'doc' })
    setRightPanelLayout('full')
  }

  // 开发产物写入完成不等于开发工作流完成；单元测试、集成测试和预览启动仍在运行时，
  // 必须禁止“进入测试阶段”提示，避免用户在上一条工作流尚未收尾时被提前切阶段。
  const developmentWorkflowRunning =
    loading ||
    Object.entries(sessionRunStates).some(([sessionId, status]) => {
      if (status !== 'running' && status !== 'stopping') return false
      const session = sessions.find((item) => item.id === sessionId)
      return Boolean(session?.pageId || session?.endpointId)
    })
  // 测试阶段“允许进入”判定：开发产物全部完成且当前开发工作流已收尾。
  const testingEntryAvailable =
    activeWorkbenchPhase === 'development' &&
    allDevelopmentModulesComplete &&
    !developmentWorkflowRunning &&
    !versionReadOnly &&
    !testingTransitionRequested
  // 测试报告通过后只开放审查入口，确认前仍停留在测试阶段。
  const reviewEntryAvailable =
    testReportPassedForEntry &&
    compareWorkbenchPhases(reachedPhase, 'review') < 0 &&
    !loading &&
    !workspaceBusy &&
    !versionReadOnly &&
    !reviewTransitionRequested

  useEffect(() => {
    // 测试报告确认后沿正常旅程自动进入审查，不要求用户再手动切换阶段。
    if (
      versionReadOnly ||
      activeWorkbenchPhase !== 'testing' ||
      !testReportPassedForEntry ||
      reviewTransitionRequested ||
      loading ||
      workspaceBusy
    ) {
      if (activeWorkbenchPhase !== 'testing') reviewCompletionPromptRef.current = ''
      return
    }
    const promptKey = `${versionViewKey}:testing-complete`
    if (reviewCompletionPromptRef.current === promptKey) return
    reviewCompletionPromptRef.current = promptKey
    setReviewCompleteModalOpen(false)
    setViewingTaskPhase('review')
    setReviewTransitionRequested(true)
    if (applicationLifecycle) {
      onApplicationLifecycleChange(beginReviewExecution(applicationLifecycle, application.id))
    }
  }, [
    activeWorkbenchPhase,
    application.id,
    applicationLifecycle,
    loading,
    onApplicationLifecycleChange,
    reviewTransitionRequested,
    testReportPassedForEntry,
    versionReadOnly,
    versionViewKey,
    workspaceBusy
  ])

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
    ? viewingTaskPhase === 'planning'
      ? '描述项目计划需要调整的页面、接口或实体范围…'
      : iterationVersion?.parentVersionId
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
  const developmentArtifactRefreshRef = useRef('')
  const developmentCompletionSnapshotRef = useRef<{
    runId: string
    status: string
    phase: string
  }>()
  useEffect(() => {
    // 构建终态在 mock/真实 AG-UI 中都可能晚于生命周期快照落盘；终态到达后主动刷新一次，
    // 让左侧产物状态、测试准入和右侧预览共同消费同一份最新规划事实。
    if (activeWorkbenchPhase !== 'development') return
    const workflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    const terminalBuild =
      workflow?.summary?.status === 'completed' &&
      ['build', 'launch_project', 'integration_test'].includes(String(workflow.summary.phase || ''))
    if (!terminalBuild) return
    const refreshKey = `${versionViewKey}:${workflow.runId}:${workflow.summary.phase}`
    if (developmentArtifactRefreshRef.current === refreshKey) return
    developmentArtifactRefreshRef.current = refreshKey
    onPlanningArtifactsRefresh()
  }, [activeWorkbenchPhase, latestWorkflowForDisplay, onPlanningArtifactsRefresh, versionViewKey])
  useEffect(() => {
    // 只记录“同一开发工作流从执行态进入完成态”的边沿，避免用户手动打开历史完成产物时误触发自动切换。
    if (activeWorkbenchPhase !== 'development' || versionReadOnly) {
      developmentCompletionSnapshotRef.current = undefined
      setDevelopmentCompletionCandidate(undefined)
      return
    }
    const workflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    const summary = workflow?.summary
    const runId = workflow?.runId || ''
    if (!summary || !runId) return
    const currentSnapshot = {
      phase: String(summary.phase || ''),
      runId,
      status: String(summary.status || '')
    }
    const previousSnapshot = developmentCompletionSnapshotRef.current
    developmentCompletionSnapshotRef.current = currentSnapshot
    const isTerminalBuild =
      currentSnapshot.status === 'completed' &&
      ['build', 'launch_project', 'integration_test'].includes(currentSnapshot.phase)
    if (
      !isTerminalBuild ||
      !previousSnapshot ||
      previousSnapshot.runId !== runId ||
      previousSnapshot.status === 'completed' ||
      !activeTargetKey
    ) {
      return
    }
    setDevelopmentCompletionCandidate({ artifactId: activeTargetKey, runId })
  }, [
    activeTargetKey,
    activeWorkbenchPhase,
    latestWorkflowForDisplay,
    versionReadOnly
  ])
  // 当前写入中的单文件变更：固定放在对话输入框上方，作为独立的授权条展示。
  const pendingCodeChangeContext = (() => {
    const workflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    const changes = workflowCodeChanges(workflow)
    const pending =
      workflow?.summary?.status === 'running' ||
      workflow?.summary?.status === 'requires_user_input'
    if (
      !pending ||
      !changes ||
      changes.files.length === 0 ||
      !isPendingDesignCodeChange(
        workflow,
        applicationLifecycle?.initialization?.stage
      )
    ) {
      return undefined
    }
    const sourceMessage = [...messages].reverse().find((message) => {
      const messageChanges = workflowCodeChanges(message.workflow)
      return messageChanges?.id === changes.id
    })
    return { changes, messageId: sourceMessage?.id, workflow }
  })()
  const pendingFileDiff = pendingCodeChangeContext
    ? {
        path: pendingCodeChangeContext.changes.files[0].path,
        diff: pendingCodeChangeContext.changes.files[0].diff,
        additions: pendingCodeChangeContext.changes.files[0].additions
      }
      : undefined
  // 测试阶段切到研发 Agent 后，受影响的开发产物临时恢复编辑权；测试 Agent 仍只读测试报告。
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
    // 测试阶段必须让用户看到正在生成的测试报告，不沿用开发阶段手动关闭面板的状态。
    if (displayIsTestingPhase) state.dismissed = false
    if (state.dismissed) return
    // 设计阶段：右侧固定「文档」区，自动落到第一份已生成产物（需求文档/项目计划）。
    // 注意：本 effect 依赖 rightPanel，必须仅在非 doc 或未选中有效文档时才 set，否则每次新建对象 →
    // rightPanel 引用变 → effect 重跑 → 再 set，形成 Maximum update depth 死循环。
    if (displayIsDesignPhase) {
      // 文档页签固定：Diff 写入过程由 DocPanel 内嵌的 FileDiffView 呈现（单文件、绿色新增行），
      // 生成/待接受期间不需要打开独立的变更审阅面板。
      state.type = 'doc'
      // active docKey 跟随旅程：生成中按 workflow.phase，就绪后按 stage（见 resolveDesignDocKey）。
      const targetKey = resolveDesignDocKey(
        applicationLifecycle?.initialization?.stage,
        activeWorkflow?.summary?.phase,
        activeWorkflow?.summary?.status === 'running',
        activeWorkbenchPhase
      )
      const currentKey = rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
      const shouldOpen = !rightPanel || rightPanel.type !== 'doc'
      const keyChanged = !shouldOpen && currentKey !== targetKey
      if (shouldOpen || keyChanged) {
        setRightPanel({ type: 'doc', docKey: targetKey })
      }
      return
    }
    // 未交付页面不能沿用此前的浏览器预览，先回到开发上下文文档，等待启动页面工作流。
    if (
      activeWorkbenchPhase === 'development' &&
      activePageOption &&
      !activePageCodeDelivered &&
      rightPanel?.type === 'preview'
    ) {
      state.type = 'doc'
      setRightPanel({ type: 'doc', docKey: 'project-plan' })
      return
    }
    // 页面/接口详细设计是工作流内部过程，不生成用户文件；开发阶段从代码 Diff 开始呈现。
    if (
      activeWorkbenchPhase === 'development' &&
      activeWorkflow?.summary?.phase === 'detail_confirmation' &&
      !pendingFileDiff
    ) {
      state.type = null
      // 详细设计仍是工作流内部过程，没有新文件时保持用户当前查看的文件页。
      return
    }
    // 代码工作流尚未产生 Diff 时不展示旧的设计文档；代码交付完成后切到源码区。
    if (
      activeWorkbenchPhase === 'development' &&
      activeWorkflow?.summary?.phase === 'build' &&
      !pendingFileDiff
    ) {
      state.type = null
      if (activeWorkflow.summary.status === 'completed') {
        if (rightPanel?.type !== 'source') setRightPanel({ type: 'source' })
      }
      // 构建节点准备下一个文件时不切走当前源码页；新 Diff 到达后由下方分支接管。
      return
    }
    // 开发阶段所有目标统一走单文件 Diff：页面、页面的直接接口依赖，以及独立接口对话都不能跳过。
    // 该判断必须早于接口文档分支和页面存在性判断，否则直接打开接口产物时右侧不会切到 Diff。
    const buildWorkflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    if (
      pendingFileDiff &&
      ['build', 'detail_confirmation', 'test_report'].includes(
        String(buildWorkflow?.summary?.phase || '')
      )
    ) {
      state.type = null
      const isTestReportDiff = buildWorkflow?.summary?.phase === 'test_report'
      const targetTab: WorkspaceTabKey = pendingFileDiff.path.startsWith('backend/')
        ? 'endpoint-source'
        : 'page-source'
      if (!isTestReportDiff && activeArtifactTab !== targetTab) setActiveArtifactTab(targetTab)
      if (rightPanel?.type !== 'source') setRightPanel({ type: 'source' })
      return
    }
    // 测试阶段右侧固定展示测试报告源码；测试节点完成前由报告构建器显示生成中占位。
    if (displayIsTestingPhase) {
      state.type = 'doc'
      if (!rightPanel || rightPanel.type !== 'doc') setRightPanel({ type: 'doc' })
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
      (activeWorkflow?.summary?.phase === 'launch_project' &&
        activeWorkflow.summary.status === 'completed') ||
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
    // 当前已有 Workflow 但没有新文件写入时，不再用通用兜底覆盖右侧已有文件。
    if (activeWorkbenchPhase === 'development' && activeWorkflow) return
    // 开发阶段没有真实的文件/Diff事件时不主动打开任何右侧内容。
    if (activeWorkbenchPhase === 'development') return
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
    displayIsTestingPhase,
    activeWorkbenchPhase,
    displayIsReviewPhase,
    versionPreviewOnly,
    versionViewKey,
    applicationLifecycle?.initialization?.stage,
    activeWorkflow?.summary?.phase,
    activeWorkflow?.summary?.status,
    // 工作流对象本身也要入依赖：构建节点的渐进变更快照只改 state.codeChanges / pendingFileDiff，
    // summary.phase/status 不变时也必须驱动右侧 Diff 面板更新（否则只显示终态）。
    latestWorkflowForDisplay,
    pendingFileDiff,
    activePageCodeDelivered
  ])

  // 页面详细设计只作为工作流内部上下文，右侧不再展示或持久化单独的设计文档。
  const designStage = applicationLifecycle?.initialization?.stage
  // 开发产物在单个版本内只解锁一次，避免生命周期事件切换时整组目录闪退再出现。
  const [developmentCatalogUnlocked, setDevelopmentCatalogUnlocked] = useState(() =>
    isDevelopmentCatalogConfirmed(designStage)
  )
  useEffect(() => {
    if (isDevelopmentCatalogConfirmed(designStage)) setDevelopmentCatalogUnlocked(true)
  }, [designStage])
  const hasDevelopmentConversation = sessions.some(
    (session) => Boolean(session.pageId) || Boolean(session.endpointId)
  )
  const developmentPromptVersionRef = useRef('')
  useEffect(() => {
    if (activeWorkbenchPhase !== 'development') {
      developmentPromptVersionRef.current = ''
      setDevelopmentConversationModalOpen(false)
      return
    }
    if (
      versionReadOnly ||
      hasPageDesigns ||
      hasDevelopmentConversation ||
      !developmentPlanningReady ||
      !developmentCatalogUnlocked ||
      developmentConversationTargetCount === 0
    ) {
      setDevelopmentConversationModalOpen(false)
      return
    }
    if (developmentPromptVersionRef.current === versionViewKey) return
    developmentPromptVersionRef.current = versionViewKey
    setDevelopmentConversationModalOpen(true)
  }, [
    activeWorkbenchPhase,
    developmentCatalogUnlocked,
    developmentConversationTargetCount,
    developmentPlanningReady,
    hasDevelopmentConversation,
    hasPageDesigns,
    versionReadOnly,
    versionViewKey
  ])
  const developmentCompletionPromptRef = useRef('')
  useEffect(() => {
    if (activeWorkbenchPhase !== 'development') {
      developmentCompletionPromptRef.current = ''
      setDevelopmentCompleteModalOpen(false)
      setTestingTransitionRequested(false)
      return
    }
    if (
      versionReadOnly ||
      developmentWorkflowRunning ||
      !allDevelopmentModulesComplete ||
      testingTransitionRequested
    ) return
    const promptKey = `${versionViewKey}:development-complete`
    if (developmentCompletionPromptRef.current === promptKey) return
    developmentCompletionPromptRef.current = promptKey
    setDevelopmentCompleteModalOpen(true)
  }, [
    activeWorkbenchPhase,
    allDevelopmentModulesComplete,
    developmentWorkflowRunning,
    testingTransitionRequested,
    versionReadOnly,
    versionViewKey
  ])
  // 向顶部阶段条上报测试阶段是否具备进入条件，供其放开测试节点点击。
  useEffect(() => {
    onTestingEntryAvailableChange?.(testingEntryAvailable)
  }, [onTestingEntryAvailableChange, testingEntryAvailable])
  useEffect(() => {
    onReviewEntryAvailableChange?.(reviewEntryAvailable)
  }, [onReviewEntryAvailableChange, reviewEntryAvailable])
  // 顶部阶段条发起进入请求时复用同一个确认弹框：只有确认动作才创建测试对话并推进阶段。
  const handledTestingEntryRequestRef = useRef(0)
  useEffect(() => {
    if (!testingEntryRequest || testingEntryRequest <= handledTestingEntryRequestRef.current) return
    handledTestingEntryRequestRef.current = testingEntryRequest
    if (!testingEntryAvailable) return
    setDevelopmentCompleteModalOpen(true)
  }, [testingEntryAvailable, testingEntryRequest])
  const handledReviewEntryRequestRef = useRef(0)
  useEffect(() => {
    if (!reviewEntryRequest || reviewEntryRequest <= handledReviewEntryRequestRef.current) return
    handledReviewEntryRequestRef.current = reviewEntryRequest
    if (!reviewEntryAvailable) return
    setReviewCompleteModalOpen(true)
  }, [reviewEntryAvailable, reviewEntryRequest])
  const activeDesignSessionSummary = activeSession
    ? sessions.find((session) => session.id === activeSession.sessionId)
    : undefined
  // 首个设计会话由首页动作创建；用户消息、Agent 消息或运行态任一出现都代表产物已开始。
  const designConversationStarted = Boolean(
    displayIsDesignPhase &&
      activeSession &&
      (messages.some((message) => message.role === 'user' || message.role === 'assistant') ||
        (activeDesignSessionSummary?.messageCount || 0) > 0 ||
        sessionRunStates[activeSession.sessionId])
  )
  const [stableDesignStatuses, setStableDesignStatuses] = useState(() => ({
    'requirement-spec': 'not-started' as WorkbenchArtifactStatus,
    'project-plan': 'not-started' as WorkbenchArtifactStatus
  }))
  const [stableDesignAvailability, setStableDesignAvailability] = useState(() => ({
    'requirement-spec': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
    'project-plan': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
  }))
  useEffect(() => {
    const requirementPath = appPath(WORKSPACE_DOC_PATHS.requirementSpec)
    const projectPlanPath = appPath(WORKSPACE_DOC_PATHS.projectPlan)
    const latestSavedAt = (session: typeof formalAnalysisSession, path: string): number =>
      (session?.savedFiles || [])
        .filter((file) => (file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)) === path)
        .reduce((latest, file) => Math.max(latest, file.savedAt), 0)
    const requirementSavedAt = latestSavedAt(formalAnalysisSession, requirementPath)
    const projectPlanSavedAt = latestSavedAt(formalPlanningSession, projectPlanPath)
    const sessionStarted = (session: typeof formalAnalysisSession): boolean =>
      Boolean(session && session.messageCount > 0)
    const analysisSessionStarted =
      sessionStarted(formalAnalysisSession) ||
      Boolean(activeSession?.sessionKind === 'analysis' && messages.length > 0)
    const planningSessionStarted =
      sessionStarted(formalPlanningSession) ||
      Boolean(activeSession?.sessionKind === 'planning' && messages.length > 0)
    // 文档写入后需求本身保持完成；只有新需求确实保存成功，才使下游项目计划重新进入进行中。
    setStableDesignStatuses({
      'requirement-spec': requirementSavedAt
        ? 'completed'
        : analysisSessionStarted
          ? 'in-progress'
          : 'not-started',
      'project-plan': projectPlanSavedAt && projectPlanSavedAt >= requirementSavedAt
        ? 'completed'
        : planningSessionStarted || requirementSavedAt > projectPlanSavedAt
          ? 'in-progress'
          : 'not-started'
    })
    // 已经生成过的文档继续保留在产物视图中，失效只体现在状态变化，不变成未来阶段灰色节点。
    setStableDesignAvailability((current) => ({
      'requirement-spec':
        current['requirement-spec'] ||
        designConversationStarted ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
      'project-plan':
        current['project-plan'] ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
    }))
  }, [
    designConversationStarted,
    designStage,
    formalAnalysisSession,
    formalPlanningSession,
    activeSession?.sessionKind,
    messages.length
  ])
  useEffect(() => {
    /** 共享路由等公共文件可能由另一个页面会话交付；状态按整个版本的正式文件快照判定。 */
    const savedFileExists = (suffix: string): boolean =>
      sessions.some((session) =>
        (session.savedFiles || []).some((file) => {
          const path = file.path.replace(/\\/g, '/').replace(/^.*?wh-branch-pms-new\//, '')
          return path === suffix || path.endsWith(`/${suffix}`)
        })
      )
    const artifactFilesComplete = (artifactId: string): boolean => {
      if (artifactId.startsWith('page:')) {
        const pageId = artifactId.replace(/^page:/, '')
        // 路由属于应用初始化脚手架，不是页面产物；页面源码授权完成即可完成页面产物。
        return savedFileExists(`frontend/pages/${pageId}.tsx`)
      }
      if (artifactId.startsWith('endpoint:')) return savedFileExists('backend/rechecks-controller.java')
      return false
    }
    const statusForArtifact = (artifactId: string): WorkbenchArtifactStatus => {
      const owners = sessions.filter((session) => artifactIdsForSession(session).includes(artifactId))
      if (owners.length === 0 || owners.every((session) => session.messageCount === 0)) {
        return 'not-started'
      }
      // 文件已授权且当前代码工作流仍在运行时保持进行中；工作流收尾后直接按正式文件快照完成。
      if (
        owners.some((session) => {
          const runStatus = sessionRunStates[session.id]
          return runStatus === 'running' || runStatus === 'stopping'
        })
      ) {
        return 'in-progress'
      }
      // 历史锁定版本是已发布快照，沿用其已完成事实；新版本绝不使用设计标记抢先完成。
      if (versionReadOnly || artifactFilesComplete(artifactId)) return 'completed'
      return 'in-progress'
    }
    setDevelopmentStatusState(() => {
      const nextStatuses: Record<string, WorkbenchArtifactStatus> = {}
      developmentPlanningPages.forEach((page) => {
        const artifactId = pageArtifactId(page.pageId)
        nextStatuses[artifactId] = statusForArtifact(artifactId)
      })
      developmentPlanningApiContracts.forEach((contract) => {
        contract.endpoints.forEach((endpoint, endpointIndex) => {
          const apiContractId = endpoint.apiContractId || contract.id
          const endpointId = endpoint.id || String(endpointIndex + 1)
          const artifactId = endpointArtifactId(apiContractId, endpointId)
          nextStatuses[artifactId] = statusForArtifact(artifactId)
        })
      })
      return { statuses: nextStatuses, versionKey: versionViewKey }
    })
  }, [
    developmentPlanningApiContracts,
    developmentPlanningPages,
    sessions,
    sessionRunStates,
    versionReadOnly,
    versionViewKey
  ])
  const developmentArtifactStatusById =
    developmentStatusState.versionKey === versionViewKey ? developmentStatusState.statuses : {}
  const developmentAutoTargets = useMemo<DevelopmentAutoTarget[]>(
    () => [
      ...developmentPlanningPages.map((page) => ({
        artifactId: pageArtifactId(page.pageId),
        kind: 'page' as const,
        page
      })),
      ...developmentPlanningApiContracts.flatMap((contract) =>
        contract.endpoints.map((endpoint) => {
          const apiContractId = endpoint.apiContractId || contract.id
          const endpointId = endpoint.id
          return {
            apiContractId,
            artifactId: endpointArtifactId(apiContractId, endpointId),
            description: endpoint.summary,
            endpointId,
            endpointLabel: `${endpoint.method} ${endpoint.path}`,
            kind: 'endpoint' as const,
            path: endpoint.path
          }
        })
      )
    ],
    [developmentPlanningApiContracts, developmentPlanningPages]
  )

  /** 会话创建后即由摘要派生“进行中”；该函数仅保留调用点语义，不再提前把产物推进为完成。 */
  const markDevelopmentArtifactsInProgress = (artifactIds: string[]): void => {
    setDevelopmentStatusState((current) => {
      const currentStatuses = current.versionKey === versionViewKey ? current.statuses : {}
      const nextStatuses = { ...currentStatuses }
      artifactIds.forEach((artifactId) => {
        nextStatuses[artifactId] =
          currentStatuses[artifactId] === 'completed' ? 'completed' : 'in-progress'
      })
      return { statuses: nextStatuses, versionKey: versionViewKey }
    })
  }
  const designDocs = (
    [
      {
        key: 'requirement-spec' as WorkspaceDocKey,
        title: '需求文档',
        path: WORKSPACE_DOC_PATHS.requirementSpec,
        content: buildRequirementSpecDoc(scenario.requirementSpec, application.name)
      },
      {
        key: 'project-plan' as WorkspaceDocKey,
        title: '项目计划',
        path: WORKSPACE_DOC_PATHS.projectPlan,
        content: buildProjectPlanDoc(scenario.projectPlan, application.name)
      }
    ] as Array<{ key: WorkspaceDocKey; title: string; path: string; content: string }>
  ).map((doc) => ({
    ...doc,
    available: stableDesignAvailability[doc.key]
  }))
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
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
  // 开发阶段：详细设计生成中（detail_confirmation running，尚未到确认卡），右侧文档区富加载占位。
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
    (compareWorkbenchPhases(reachedPhase, 'review') >= 0 &&
      sessions.some(
        (session) => (session.title || '').includes('代码审查') && session.messageCount > 0
      ))
  const hasSavedWorkspaceFile = (path: string): boolean =>
    sessions.some((session) =>
      (session.savedFiles || []).some(
        (file) => (file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)) === path
      )
    )
  const testingSession = sessions.find(
    (session) => session.sessionKind === 'testing' || (session.title || '').includes('应用测试')
  )
  const reviewSession = sessions.find(
    (session) => session.sessionKind === 'review' || (session.title || '').includes('代码审查')
  )
  const testReportFileSaved = hasSavedWorkspaceFile(appPath(WORKSPACE_DOC_PATHS.testReport))
  const reviewReportFileSaved = hasSavedWorkspaceFile(appPath(WORKSPACE_DOC_PATHS.codeReview))
  const reviewNeedsCompletionRepair = Boolean(
    activeWorkflow?.summary?.phase === 'code_review' &&
      reviewReportClarification?.mode === 'code_review' &&
      reviewReportFileSaved &&
      !pendingCodeChangeContext
  )
  const reviewCompletionRepairRef = useRef('')
  useEffect(() => {
    if (!reviewNeedsCompletionRepair || !applicationLifecycle) {
      if (!reviewNeedsCompletionRepair) reviewCompletionRepairRef.current = ''
      return
    }
    const repairKey = `${versionViewKey}:${activeWorkflow?.runId || ''}`
    if (!repairKey || reviewCompletionRepairRef.current === repairKey) return
    reviewCompletionRepairRef.current = repairKey
    onApplicationLifecycleChange(completeReviewExecution(applicationLifecycle, application.id))
  }, [
    activeWorkflow?.runId,
    application.id,
    applicationLifecycle,
    onApplicationLifecycleChange,
    reviewNeedsCompletionRepair,
    versionViewKey
  ])
  const testReportAvailable =
    compareWorkbenchPhases(reachedPhase, 'testing') >= 0 || Boolean(testingSession?.messageCount)
  const testReportOutcome = testReportSnapshot?.status || ''
  const testReportFailed = ['failed', 'unqualified', '不合格'].includes(testReportOutcome)
  const testReportPassed = ['passed', 'qualified', '合格'].includes(testReportOutcome)
  const testReportReady = testReportFileSaved && !testReportFailed && testReportPassed
  const testReportArtifactStatus: WorkbenchArtifactStatus = !testingSession?.messageCount
    ? 'not-started'
    : testReportReady
      ? 'completed'
      : 'in-progress'
  const reviewArtifactStatus: WorkbenchArtifactStatus = !reviewSession?.messageCount
    ? 'not-started'
    : reviewReportFileSaved && reviewArtifactReady
      ? 'completed'
      : 'in-progress'
  // 一个开发对话可以同时关联页面和接口，右侧分别保留各自的源码与设计文件。
  const pageSource =
    conversationPageOption &&
    isPageCodeDelivered(conversationPageOption.pageId) &&
    conversationPageDesign
      ? buildPageSource(conversationPageDesign, conversationPageOption.pageId || 'page')
      : undefined
  const endpointSource = conversationEndpointDesign
    ? buildEndpointSource(conversationEndpointDesign)
    : undefined
  const [activeArtifactTab, setActiveArtifactTab] = useState<WorkspaceTabKey>('page-source')
  const activeSource =
    activeArtifactTab === 'endpoint-source' ? endpointSource : pageSource || endpointSource
  // 右侧工程文件树只消费已经授权保存的文件；工作流中的 Diff 由 pendingFileDiff 单独覆盖展示。
  // 这避免需求/计划/代码在尚未生成时就以静态骨架的形式“提前存在”。
  const workspaceSourceFiles = useMemo(() => {
    const filesByPath = new Map(workspaceScaffoldFiles.map((file) => [file.path, file]))
    sessions
      .flatMap((session) => session.savedFiles || [])
      .sort((left, right) => left.savedAt - right.savedAt)
      .forEach((file) => {
        const path = file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)
        filesByPath.set(path, { path, content: file.content })
      })
    return [...filesByPath.values()]
  }, [sessions])
  const savedFileContentByPath = useMemo(
    () => new Map(workspaceSourceFiles.map((file) => [file.path, file.content])),
    [workspaceSourceFiles]
  )
  // 页面任务独立「页面预览」：路由到当前页面 path，与应用预览（应用根）区分。
  const pagePreviewPath = conversationPageOption?.path || activeHeaderTarget.path || '/'
  const pagePreviewUrl = composeVersionPreviewUrl(
    runtimePreviewBaseUrl,
    pagePreviewPath,
    versionViewKey
  )
  const pagePreviewReady = Boolean(
    conversationPageOption &&
      isPageCodeDelivered(conversationPageOption.pageId) &&
      pagePreviewUrl
  )
  const browserTab: WorkspaceTab = {
    key: displayIsAcceptancePhase ? 'preview' : 'page-preview',
    label: '浏览器',
    available: displayIsAcceptancePhase ? Boolean(previewTabUrl) : pagePreviewReady,
    icon: 'browser'
  }
  const acceptancePreviewTab: WorkspaceTab | undefined = displayIsAcceptancePhase
    ? {
        key: 'application-preview',
        label: '应用预览',
        available: Boolean(applicationPreviewUrl),
        icon: 'application'
      }
    : undefined
  // 右侧按功能类别收敛：应用文件（应用目录：文档与代码同一棵树）在前，浏览器（页面预览）在后。
  const workspaceTabs: WorkspaceTab[] = [
    { key: 'project' as const, label: '应用文件', available: true, icon: 'project' as const },
    browserTab,
    ...(acceptancePreviewTab ? [acceptancePreviewTab] : [])
  ]
  // 当前激活的工作区 tab：预览面板 → 浏览器；文档/源码面板统一归入“应用文件”。
  const activeWorkspaceTab: WorkspaceTabKey =
    rightPanel?.type === 'preview'
      ? rightPanel.requestKey?.endsWith(':page')
        ? ('page-preview' as WorkspaceTabKey)
        : displayIsAcceptancePhase
          ? rightPanel.requestKey?.endsWith(':acceptance')
            ? 'application-preview'
            : 'preview'
          : 'preview'
      : 'project'
  const isAcceptancePreviewTab =
    displayIsAcceptancePhase && activeWorkspaceTab === 'application-preview'
  const openWorkspaceTab = (key: WorkspaceTabKey): void => {
    // 手动切换 Tab 后不再自动覆盖用户当前查看内容，面板尺寸始终由三档控制器决定。
    autoOpenStateRef.current.type = null
    autoOpenStateRef.current.dismissed = true
    if (key === 'page-preview' && pagePreviewUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:page`,
        url: pagePreviewUrl
      })
    } else if (key === 'application-preview' && applicationPreviewUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:acceptance`,
        url: applicationPreviewUrl
      })
    } else if (key === 'preview' && previewTabUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:application`,
        url: previewTabUrl
      })
    } else if (key === 'project') {
      // 应用文件页签：右侧展示应用目录（文档+代码），并定位到当前产物对应的文件。
      setRightPanel({ type: activeSource ? 'source' : 'doc' })
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
            path: WORKSPACE_DOC_PATHS.codeReview,
            status: reviewArtifactStatus === 'completed' ? '已完成' : '进行中',
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
                    isPageCodeDelivered(conversationPageOption.pageId)
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
                    isEndpointCodeDelivered(conversationEndpointOption.endpoint.id)
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
      phase: doc.key === 'project-plan' ? ('planning' as const) : ('analysis' as const),
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
          status: developmentArtifactStatusById[pageArtifactId(page.pageId)] || 'not-started',
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
                developmentArtifactStatusById[endpointArtifactId(apiContractId, endpointId)] ||
                'not-started',
              type: 'endpoint' as const,
              available: true
            }
          })
        )
      : []),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningEntities.map((entity) => ({
          id: entityArtifactId(entity.entityId),
          name: entity.label,
          path: entity.schemaRef || `entities/${entity.entityId}`,
          phase: 'development' as const,
          status: 'not-started' as const,
          type: 'entity' as const,
          available: true
        }))
      : []),
    {
      id: documentArtifactId('test-report'),
      name: '测试报告',
      path: WORKSPACE_DOC_PATHS.testReport,
      phase: 'testing',
      status: testReportArtifactStatus,
      type: 'document',
      available: testReportAvailable
    },
    {
      id: documentArtifactId('code-review'),
      name: '审查报告',
      path: WORKSPACE_DOC_PATHS.codeReview,
      phase: 'review',
      status: reviewArtifactStatus,
      type: 'document',
      available: compareWorkbenchPhases(reachedPhase, 'review') >= 0
    }
  ]
  const artifactOwners = resolveArtifactOwners(
    sessions.map((session) => ({
      artifactIds: artifactIdsForSession(session),
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
        reachedPhase,
        versionLocked: versionReadOnly
      })
    ])
  ) as Record<string, WorkbenchArtifactAccess>
  const activeSessionSummary = sessions.find((session) => session.id === activeSessionId)
  const activeSessionArtifactIds = activeSessionSummary
    ? artifactIdsForSession(activeSessionSummary)
    : []
  const composerArtifactResources = artifactCatalog.map((artifact) => ({
    accessMessage: artifactAccessById[artifact.id].message,
    accessMode: artifactAccessById[artifact.id].mode,
    attached: activeSessionArtifactIds.includes(artifact.id),
    id: artifact.id,
    name: artifact.name,
    path: artifact.path,
    type: artifact.type
  }))
  const artifactById = new Map(artifactCatalog.map((artifact) => [artifact.id, artifact]))
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
            type: artifact.type === 'entity' ? ('document' as const) : artifact.type
          }))
      : conversationArtifactsBase
  ).map((artifact) => ({
    ...artifact,
    accessMessage: artifactAccessById[artifact.id]?.message,
    accessMode: artifactAccessById[artifact.id]?.mode
  }))
  const visibleConversationArtifacts = designSessionSwitching ? [] : conversationArtifacts
  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    if (activeApiEndpoint) return
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
  }, [activeApiEndpoint, developmentPlanningPages])

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
    // 应用配置只是覆盖中间工作区的临时抽屉，不改变用户对右侧产物面板的开关选择。
    setActiveView('settings')
  }

  /** 从目录恢复任意历史会话，并同步其任务级别而不改变顶部阶段状态。 */
  const handleOpenSidebarSession = async (sessionId: string): Promise<void> => {
    const session = sessions.find((item) => item.id === sessionId)
    if (session?.pageId || session?.endpointId) {
      setViewingTaskPhase(activeWorkbenchPhase === 'testing' ? 'testing' : 'development')
    }
    else if (session?.sessionKind === 'review' || (session?.title || '').includes('代码审查')) {
      setViewingTaskPhase('review')
    } else if (session?.sessionKind === 'acceptance') {
      setViewingTaskPhase('acceptance')
    }
    else if (session?.sessionKind === 'testing' || (session?.title || '').includes('应用测试')) {
      setViewingTaskPhase('testing')
    } else if (
      session?.sessionKind === 'planning' ||
      (session?.title || '') === '项目计划'
    ) {
      setViewingTaskPhase('planning')
    } else if (
      session?.sessionKind === 'analysis' ||
      (session?.title || '').includes('需求分析')
    ) {
      setViewingTaskPhase('analysis')
    }
    else setViewingTaskPhase(activeWorkbenchPhase)
    setActiveView('chat')
    await handleOpenChatSession(sessionId)
  }

  /** 新建普通对话时退出页面/API 目标上下文，避免后续消息被旧目标接管。 */
  const handleCreateChatSession = (): void => {
    setViewingTaskPhase(activeWorkbenchPhase)
    setActiveView('chat')
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleCreateSessionFromList(renderedTaskPhase)
  }

  /** 在目标会话创建成功后持久化开发 Agent 的首条详细设计消息，模板卡只从该消息渲染。 */
  const appendInitialDetailAgentMessage = async (
    identity: SessionIdentity,
    detailBlocker: DetailBlockerMessage
  ): Promise<void> => {
    const existingMessages = getSessionMessages(identity.key)
    if (existingMessages.length > 0) return
    const nextMessages: AgentChatMessage[] = [
      {
        id: Date.now(),
        role: 'assistant',
        agentPhase: 'development',
        content: '',
        detailBlocker,
        createdAt: Date.now()
      }
    ]
    setSessionMessages(identity.key, nextMessages)
    await persistSession({
      artifactIds: identity.artifactIds,
      editorMode: identity.editorMode,
      messages: nextMessages,
      sessionId: identity.sessionId,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      pageId: identity.pageId,
      sessionKind: identity.sessionKind,
      titleFrom: identity.pageId || identity.endpointLabel || '开发对话'
    })
  }

  /** 为首次选择的页面产物创建会话并立即启动首轮 Agent 工作流。 */
  const createPageConversation = async (
    page: DevelopmentPlanningPageOption
  ): Promise<boolean> => {
    setActiveView('chat')
    setActiveDetailTarget({ type: 'page', pageId: page.pageId })
    // 创建并持久化页面会话后，由开发 Agent 的首条消息给出模板选择。
    const identity = await createPageSession(page.pageId, page.label)
    await appendInitialDetailAgentMessage(identity, {
      type: 'page',
      pageId: page.pageId,
      label: page.label,
      path: page.path,
      purpose: page.purpose
    })
    markDevelopmentArtifactsInProgress([pageArtifactId(page.pageId)])
    return true
  }

  /** 为首次选择的接口产物创建会话并立即启动首轮 Agent 工作流。 */
  const createEndpointConversation = async (target: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }): Promise<boolean> => {
    setActiveView('chat')
    setActiveDetailTarget({
      type: 'endpoint',
      apiContractId: target.apiContractId,
      endpointId: target.endpointId,
      endpointKey: `${target.apiContractId}:${target.endpointId}`,
      label: target.endpointLabel
    })
    const endpoint = developmentPlanningApiContracts
      .find((contract) => contract.id === target.apiContractId)
      ?.endpoints.find((item) => item.id === target.endpointId)
    const identity = await createEndpointSession(
      target.apiContractId,
      target.endpointId,
      target.endpointLabel
    )
    await appendInitialDetailAgentMessage(identity, {
      type: 'endpoint',
      apiContractId: target.apiContractId,
      endpointId: target.endpointId,
      label: target.endpointLabel,
      path: endpoint?.path,
      purpose: endpoint?.summary
    })
    setViewingTaskPhase(activeWorkbenchPhase === 'testing' ? 'testing' : 'development')
    markDevelopmentArtifactsInProgress([
      endpointArtifactId(target.apiContractId, target.endpointId)
    ])
    return true
  }

  /** 点击未开始页面时仅请求授权，确认前不创建会话也不改变产物状态。 */
  const handleCreatePageTask = (page: DevelopmentPlanningPageOption): void => {
    setPendingDevelopmentConversationTarget({
      description: page.purpose,
      id: `page:${page.pageId}`,
      kind: 'page',
      label: page.label,
      pageId: page.pageId,
      path: page.path
    })
  }

  /** 点击未开始接口时仅请求授权，确认前不创建会话也不改变产物状态。 */
  const handleCreateEndpointTask = (target: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }): void => {
    const endpoint = developmentPlanningApiContracts
      .find((contract) => contract.id === target.apiContractId)
      ?.endpoints.find((item) => item.id === target.endpointId)
    setPendingDevelopmentConversationTarget({
      apiContractId: target.apiContractId,
      description: endpoint?.summary,
      endpointId: target.endpointId,
      id: `endpoint:${target.apiContractId}:${target.endpointId}`,
      kind: 'endpoint',
      label: target.endpointLabel,
      path: endpoint?.path || target.endpointLabel.replace(/^\S+\s+/, '')
    })
  }

  /** 把首次开发弹框选择解析回计划产物，并复用统一的产物对话创建入口。 */
  const handleCreateInitialDevelopmentConversation = async (
    target: DevelopmentConversationTarget
  ): Promise<void> => {
    let startTask: Promise<boolean>
    if (target.kind === 'page') {
      const pageId = target.pageId || target.id.replace(/^page:/, '')
      const page = developmentPlanningPages.find((item) => item.pageId === pageId)
      if (!page) return
      startTask = createPageConversation(page)
    } else {
      const apiContractId = target.apiContractId || ''
      const endpointId = target.endpointId || ''
      if (!apiContractId || !endpointId) return
      startTask = createEndpointConversation({
        apiContractId,
        endpointId,
        endpointLabel: target.label
      })
    }
    // 先完成正式会话创建，再关闭弹框并切换底下的对话，避免出现空白“新对话”。
    try {
      const started = await startTask
      if (!started) {
        setPreviewError('开发对话启动失败，请重试。')
        return
      }
      setViewingTaskPhase(activeWorkbenchPhase === 'testing' ? 'testing' : 'development')
      setDevelopmentConversationModalOpen(false)
    } catch (caughtError) {
      setPreviewError(caughtError instanceof Error ? caughtError.message : '开发对话启动失败，请重试。')
    }
  }

  /** 自动选中下一个开发产物；没有既有会话时只弹出创建确认，不直接创建会话。 */
  const openNextDevelopmentTarget = async (target: DevelopmentAutoTarget): Promise<void> => {
    setActiveView('chat')
    setViewingTaskPhase('development')
    setGeneratingDetailTargetKey('')
    if (target.kind === 'page') {
      setActiveArtifactTab('page-source')
      setInteractingDetailTargetKey(pageDetailTargetKey(target.page.pageId))
      setActiveDetailTarget({ type: 'page', pageId: target.page.pageId })
      const ownerSessionId = artifactOwners[target.artifactId]
      if (ownerSessionId) {
        await handleOpenSession(ownerSessionId)
        return
      }
      setPendingDevelopmentConversationTarget({
        description: target.page.purpose,
        id: `page:${target.page.pageId}`,
        kind: 'page',
        label: target.page.label,
        pageId: target.page.pageId,
        path: target.page.path
      })
      return
    }
    setActiveArtifactTab('endpoint-source')
    setRightPanel({ type: 'source' })
    setRightPanelLayout('split')
    setInteractingDetailTargetKey(
      endpointDetailTargetKey(target.apiContractId, target.endpointId)
    )
    setActiveDetailTarget({
      type: 'endpoint',
      apiContractId: target.apiContractId,
      endpointId: target.endpointId,
      endpointKey: `${target.apiContractId}:${target.endpointId}`,
      label: target.endpointLabel
    })
    const ownerSessionId = artifactOwners[target.artifactId]
    if (ownerSessionId) {
      await handleOpenSession(ownerSessionId)
      return
    }
    setPendingDevelopmentConversationTarget({
      apiContractId: target.apiContractId,
      description: target.description,
      endpointId: target.endpointId,
      id: `endpoint:${target.apiContractId}:${target.endpointId}`,
      kind: 'endpoint',
      label: target.endpointLabel,
      path: target.path
    })
  }

  useEffect(() => {
    // 开发产物完成后自动推进一次；没有后续产物时交给现有“开发完成”门禁处理。
    if (
      !developmentCompletionCandidate ||
      activeWorkbenchPhase !== 'development' ||
      versionReadOnly ||
      developmentArtifactStatusById[developmentCompletionCandidate.artifactId] !== 'completed'
    ) {
      return
    }
    const currentIndex = developmentAutoTargets.findIndex(
      (target) => target.artifactId === developmentCompletionCandidate.artifactId
    )
    const nextTarget =
      developmentAutoTargets
        .slice(Math.max(0, currentIndex + 1))
        .find((target) => developmentArtifactStatusById[target.artifactId] !== 'completed') ||
      developmentAutoTargets.find(
        (target) => developmentArtifactStatusById[target.artifactId] !== 'completed'
      )
    setDevelopmentCompletionCandidate(undefined)
    if (!nextTarget) return
    openNextDevelopmentTarget(nextTarget).catch((caughtError) => {
      setPreviewError(caughtError instanceof Error ? caughtError.message : '下一个开发产物启动失败，请重试。')
    })
  }, [
    activeWorkbenchPhase,
    developmentArtifactStatusById,
    developmentAutoTargets,
    developmentCompletionCandidate,
    versionReadOnly
  ])

  /** 确认单个产物的授权弹窗，并复用首次开发选择的统一创建流程。 */
  const handleConfirmArtifactConversation = async (): Promise<void> => {
    if (!pendingDevelopmentConversationTarget) return
    await handleCreateInitialDevelopmentConversation(pendingDevelopmentConversationTarget)
    setPendingDevelopmentConversationTarget(undefined)
  }

  /** 将输入框中明确添加的可写产物归入当前对话，并推进为进行中。 */
  const handleAttachArtifactToConversation = async (artifactId: string): Promise<void> => {
    await attachArtifactsToActiveSession([artifactId])
    markDevelopmentArtifactsInProgress([artifactId])
  }

  /** 用户确认后先推进测试阶段，再由测试 Agent 创建应用级测试对话。 */
  const handleConfirmDevelopmentComplete = (): void => {
    setDevelopmentCompleteModalOpen(false)
    setViewingTaskPhase('testing')
    if (applicationLifecycle) {
      onApplicationLifecycleChange(beginTestingExecution(applicationLifecycle, application.id))
    }
    setTestingTransitionRequested(true)
  }

  /** 用户确认测试结果后才推进审查阶段，并由审查 Agent 创建应用级会话。 */
  const handleConfirmTestingComplete = (): void => {
    setReviewCompleteModalOpen(false)
    setViewingTaskPhase('review')
    setReviewTransitionRequested(true)
    if (applicationLifecycle) {
      onApplicationLifecycleChange(beginReviewExecution(applicationLifecycle, application.id))
    }
  }

  /** 用户在应用预览内确认验收通过后写入当前版本生命周期，生成版本门禁随之开放。 */
  const handleAcceptApplication = (): void => {
    if (!applicationLifecycle || versionReadOnly) return
    onApplicationLifecycleChange(
      completeAcceptanceExecution(applicationLifecycle, application.id)
    )
  }

  /** 点击不通过后切回验收对话，由产品 Agent 提示用户输入验收意见。 */
  const handleSubmitAcceptanceFeedback = (): void => {
    if (versionReadOnly) return
    setActiveView('chat')
    setRightPanelLayout('split')
    // 先确保验收默认会话已经成为当前会话，再发起一次不带用户正文的 Agent 提示。
    void createAcceptanceSession()
      .then((identity) =>
        handleSend(undefined, '不通过验收', {
          sessionIdentity: identity,
          suppressUserMessage: true
        })
      )
      .catch(() => undefined)
  }

  /** 从应用大纲切换页面；没有消息历史时仅展示空白上下文，不提前创建会话。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    // 页面与接口可以共用一个会话；切换产物时右侧源码页签也必须同步切换。
    setActiveArtifactTab('page-source')
    setViewingTaskPhase(activeWorkbenchPhase === 'testing' ? 'testing' : 'development')
    setPreviewError('')
    const completedPreviewUrl =
      isPageCodeDelivered(page.pageId)
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
      setRightPanelLayout('split')
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

  // 阶段只负责初始化默认会话或恢复该阶段最近会话；消息发送后不再由阶段逻辑抢占当前会话。
  const phaseSwitchHandledRef = useRef<WorkbenchPhase | ''>('')
  useEffect(() => {
    // 必须先完成会话目录加载，随后由阶段规则明确决定要打开哪一条会话。
    if (loadingSessions) return
    if (phaseSwitchHandledRef.current === activeWorkbenchPhase) return
    phaseSwitchHandledRef.current = activeWorkbenchPhase

    if (activeWorkbenchPhase === 'development') {
      // 开发阶段按页面/API 产物选择会话，不用阶段级默认会话覆盖用户当前选择。
      setViewingTaskPhase('development')
      const developmentSession = sessions
        .filter((session) => Boolean(session.pageId) || Boolean(session.endpointId))
        .sort((left, right) => right.updatedAt - left.updatedAt)[0]
      if (developmentSession && developmentSession.id !== activeSessionId) {
        handleOpenChatSession(developmentSession.id).catch(() => undefined)
      }
      return
    }

    setViewingTaskPhase(activeWorkbenchPhase)
    if (activeWorkbenchPhase === 'analysis' || activeWorkbenchPhase === 'planning') {
      setRightPanel({
        type: 'doc',
        docKey: activeWorkbenchPhase === 'planning' ? 'project-plan' : 'requirement-spec'
      })
      setRightPanelLayout('split')
    } else if (activeWorkbenchPhase === 'review') {
      // 审查阶段默认展示审查报告；应用预览由独立页签承载。
      setRightPanel({ type: 'doc' })
    }

    const stageSession = latestStageSession(sessions, activeWorkbenchPhase)
    if (stageSession) {
      handleOpenChatSession(stageSession.id).catch(() => undefined)
      return
    }

    const createDefaultSession =
      activeWorkbenchPhase === 'analysis'
        ? ensureAnalysisSession
        : activeWorkbenchPhase === 'planning'
          ? ensurePlanningSession
          : activeWorkbenchPhase === 'testing'
            ? createTestingSession
            : activeWorkbenchPhase === 'review'
              ? createReviewSession
              : createAcceptanceSession
    createDefaultSession()
      .then((identity) => handleOpenChatSession(identity.sessionId))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeWorkbenchPhase,
    activeSessionId,
    ensureAnalysisSession,
    ensurePlanningSession,
    createAcceptanceSession,
    createReviewSession,
    createTestingSession,
    loadingSessions,
    sessions
  ])
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
  const handleDesignArtifactSelect = async (docKey: SidebarDocumentKey): Promise<void> => {
    setActiveView('chat')
    setViewingTaskPhase(
      docKey === 'code-review'
        ? 'review'
        : docKey === 'test-report'
          ? 'testing'
          : docKey === 'project-plan'
            ? 'planning'
            : 'analysis'
    )
    autoOpenStateRef.current.dismissed = true
    setRightPanel(
      docKey === 'code-review' || docKey === 'test-report' ? { type: 'doc' } : { type: 'doc', docKey }
    )
    setRightPanelLayout('split')
    const ownerSessionId = artifactOwners[documentArtifactId(docKey)]
    const defaultSession = sessions.find((session) => session.id === ownerSessionId)
    if (defaultSession && defaultSession.id !== activeSessionId) {
      await handleOpenChatSession(defaultSession.id)
    }
  }

  /** 从文档产物菜单创建草稿对话，并保持文档阶段上下文。 */
  const handleCreateDocumentTask = (docKey: SidebarDocumentKey): void => {
    handleCreateChatSession()
    setViewingTaskPhase(
      docKey === 'code-review'
        ? 'review'
        : docKey === 'test-report'
          ? 'testing'
          : docKey === 'project-plan'
            ? 'planning'
            : 'analysis'
    )
    setRightPanel(
      docKey === 'code-review' || docKey === 'test-report' ? { type: 'doc' } : { type: 'doc', docKey }
    )
  }

  /** 从应用大纲切换 API；页面和 API 目标互斥，因此会清空当前页面选中态。 */
  const handleApiEndpointSelect = (target: ActiveApiEndpointTarget): void => {
    // 同一开发会话的接口产物不能只改变左侧选中态，右侧应立即定位到接口源码。
    setActiveArtifactTab('endpoint-source')
    setRightPanel({ type: 'source' })
    setRightPanelLayout('split')
    setViewingTaskPhase(activeWorkbenchPhase === 'testing' ? 'testing' : 'development')
    setPreviewError('')
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
    const pendingChanges = workflowCodeChanges(workflow)
    const clarificationMode = String(
      (workflow.state?.clarification as { mode?: unknown } | undefined)?.mode ||
        (workflow.result?.clarification as { mode?: unknown } | undefined)?.mode ||
        ''
    )
    // 正式文件必须先经过 Diff 接受；不能用需求/计划确认卡直接跳过文件写入门槛。
    if (
      pendingChanges?.files.length &&
      ['requirements', 'project_planning', 'test_report', 'code_review'].includes(
        String(workflow.summary?.phase || '')
      ) &&
      clarificationMode !== 'file_acceptance' &&
      typeof answers.file_acceptance !== 'string'
    ) {
      setPreviewError('请先在右侧确认文件 Diff，文件保存后才能确认并进入下一阶段。')
      return
    }
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

  /** 接受固定在输入框上方的单文件变更授权，不把授权动作混入消息流。 */
  const handleApprovePendingCodeChange = (): void => {
    const context = pendingCodeChangeContext
    const file = context?.changes.files[0]
    if (!context?.workflow || !file || !activeSession) return
    const projectPath = file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)
    void recordAcceptedFile(activeSession.sessionId, {
      path: file.path,
      content: contentFromFileDiff(file.diff, savedFileContentByPath.get(projectPath) || '')
    })
      .then(() => handleSubmitWorkflowClarification(context.workflow, { file_acceptance: file.path }))
      .catch((caughtError) => {
        setPreviewError(
          caughtError instanceof Error ? caughtError.message : '保存文件确认结果失败。'
        )
      })
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
    activeView === 'chat' && rightPanelLayout !== 'hidden' && Boolean(rightPanel)
  // 项目计划一经确认，开发目录即保持可用；刷新期间不再用瞬时 planningReady 灰化产物。
  const showDevelopmentTasks = developmentCatalogUnlocked
  // 测试阶段的对话导航只展示测试会话，隔离分析/计划/开发/审查阶段的历史会话。
  const navigationSessions = displayIsTestingPhase
    ? sessions.filter(
        (session) => session.sessionKind === 'testing' || (session.title || '').includes('应用测试')
      )
    : sessions
  // 左侧设计文档只允许选中已生成的产物；右侧旧面板若指向未来文档，自动回到当前阶段文档。
  const selectableDesignDocKeys = new Set(
    designDocs
      .filter((doc) => doc.available && stableDesignStatuses[doc.key] !== 'not-started')
      .map((doc) => doc.key)
  )
  const phaseDesignDocKey: WorkspaceDocKey =
    activeWorkbenchPhase === 'planning' ? 'project-plan' : 'requirement-spec'
  const explicitDesignDocSelection =
    displayIsDesignPhase && viewingTaskPhase !== activeWorkbenchPhase
  const selectedDesignDocKey =
    displayIsDesignPhase
      ? explicitDesignDocSelection &&
        activeDesignDocKey &&
        selectableDesignDocKeys.has(activeDesignDocKey)
        ? activeDesignDocKey
        : selectableDesignDocKeys.has(phaseDesignDocKey)
          ? phaseDesignDocKey
          : undefined
      : undefined
  const activeEditableArtifactId = displayIsDesignPhase
    ? selectedDesignDocKey
      ? documentArtifactId(selectedDesignDocKey)
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
  // 项目页签定位的文件：设计/审查按当前文档，开发按当前会话产物源码；随左侧产物选择自动切换。
  const projectInitialFilePath = displayIsDesignPhase
    ? selectedDesignDocKey === 'project-plan'
      ? appPath(WORKSPACE_DOC_PATHS.projectPlan)
      : appPath(WORKSPACE_DOC_PATHS.requirementSpec)
    : displayIsTestingPhase
        ? appPath(WORKSPACE_DOC_PATHS.testReport)
        : displayIsReviewPhase
          ? appPath(WORKSPACE_DOC_PATHS.codeReview)
          : displayIsAcceptancePhase
            ? appPath(WORKSPACE_DOC_PATHS.requirementSpec)
          : activeSource
            ? appPath(activeSource.filePath)
            : ''
  const selectedProjectFilePath = projectInitialFilePath
  const projectInitialFileExists = Boolean(
    selectedProjectFilePath && savedFileContentByPath.has(selectedProjectFilePath)
  )

  /** 项目树中 Markdown 文档的编辑配置：设计文档带草稿编辑（只读按阶段权限），审查报告只读。 */
  const projectDocConfig = (path: string): ProjectDocumentConfig | undefined => {
    if (
      path === appPath(WORKSPACE_DOC_PATHS.requirementSpec) ||
      path === appPath(WORKSPACE_DOC_PATHS.projectPlan)
    ) {
      const docKey: WorkspaceDocKey =
        path === appPath(WORKSPACE_DOC_PATHS.projectPlan) ? 'project-plan' : 'requirement-spec'
      const savedContent = savedFileContentByPath.get(path)
      const editable = displayIsDesignPhase && !artifactEditorReadOnly
      return {
        content: editedDesignDocs[docKey] ?? savedContent ?? '',
        readOnly: !editable,
        onSaveEdit: editable
          ? (draft: string) => setEditedDesignDocs((prev) => ({ ...prev, [docKey]: draft }))
          : undefined
      }
    }
    if (path === appPath(WORKSPACE_DOC_PATHS.testReport)) {
      return { content: savedFileContentByPath.get(path) || '', readOnly: true }
    }
    if (path === appPath(WORKSPACE_DOC_PATHS.codeReview)) {
      return {
        content: savedFileContentByPath.get(path) || '',
        readOnly: true
      }
    }
    return undefined
  }
  return (
    <section
      className={cx(
        'ai-chat-panel',
        showRightPanel && 'embedded-preview-open',
        rightPanelLayout === 'full' && 'right-panel-full',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        elementInspectionActive && 'element-inspection-active',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <DevelopmentConversationModal
        onCancel={() => setDevelopmentConversationModalOpen(false)}
        onConfirm={handleCreateInitialDevelopmentConversation}
        open={developmentConversationModalOpen}
        tree={developmentConversationTree}
      />
      <DevelopmentArtifactConversationConfirmModal
        onCancel={() => setPendingDevelopmentConversationTarget(undefined)}
        onConfirm={handleConfirmArtifactConversation}
        open={Boolean(pendingDevelopmentConversationTarget)}
        target={pendingDevelopmentConversationTarget}
      />
      <DevelopmentStageCompleteModal
        endpointCount={planningContracts.reduce(
          (total, contract) => total + contract.endpoints.length,
          0
        )}
        onCancel={() => setDevelopmentCompleteModalOpen(false)}
        onConfirm={handleConfirmDevelopmentComplete}
        open={developmentCompleteModalOpen}
        pageCount={planningPages.length}
      />
      <TestingStageCompleteModal
        onCancel={() => setReviewCompleteModalOpen(false)}
        onConfirm={handleConfirmTestingComplete}
        open={reviewCompleteModalOpen}
      />
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
                available: testReportAvailable,
                key: 'test-report' as const,
                label: '测试报告',
                path: WORKSPACE_DOC_PATHS.testReport,
                status: testReportArtifactStatus
              },
              {
          available: compareWorkbenchPhases(reachedPhase, 'review') >= 0,
                key: 'code-review' as const,
                label: '审查报告',
                path: WORKSPACE_DOC_PATHS.codeReview,
                status: reviewArtifactStatus
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
            entities={developmentPlanningEntities}
            artifactStatusById={developmentArtifactStatusById}
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
              displayIsTestingPhase && rightPanel?.type === 'doc'
                ? 'test-report'
                : displayIsReviewPhase && rightPanel?.type === 'doc'
                ? 'code-review'
                : selectedDesignDocKey
            }
            selectedPageId={
              activePageId ||
              (activeDetailTarget.type === 'none' ? activeSession?.pageId || '' : '')
            }
            sessions={navigationSessions}
            showDevelopmentTasks={showDevelopmentTasks}
            skillsActive={activeView === 'skills'}
            settingsActive={activeView === 'settings'}
          />
          <div className={cx('ai-chat-assistant')}>
            {activeView === 'skills' ? (
              <SkillsPage onSkillDisabled={handleSkillDisabled} />
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
                    artifacts={visibleConversationArtifacts}
                    conversationTitle={
                      (designSessionSwitching
                        ? renderedTaskPhase === 'planning'
                          ? '项目计划'
                          : '需求分析'
                        : sessions.find((session) => session.id === activeSessionId)?.title) ||
                      (displayIsDesignPhase
                        ? renderedTaskPhase === 'planning'
                          ? '项目计划'
                          : '需求分析'
                        : displayIsReviewPhase
                          ? '代码审查'
                          : displayIsAcceptancePhase
                            ? '应用验收'
                            : '新对话')
                    }
                    historical={viewingHistoricalStage}
                  />
                ) : null}
                {viewingHistoricalStage && activeView === 'chat' ? (
                  <div className={cx('historical-task-notice')} role="status">
                    <span>
                      正在查看历史阶段任务，顶部仍处于
                      {WORKBENCH_PHASE_AGENTS[activeWorkbenchPhase].label}
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
                  agentPhase={renderedTaskPhase}
                  applicationLifecycle={applicationLifecycle}
                  conversationPhase={conversationPhase}
                  interactionsDisabled={versionReadOnly || viewingHistoricalStage}
                  key={`${activeSession?.key || draftKey}:${renderedTaskPhase}`}
                  loading={designSessionSwitching || loading}
                  messages={designSessionSwitching ? [] : messages}
                  onDiscardArtifact={handleDiscardArtifact}
                  onSubmitClarification={handleSubmitWorkflowClarification}
                  onStartDetailDesign={handleStartDetailDesign}
                />

                <div className={cx('ai-chat-composer-area')}>
                  {pendingCodeChangeContext ? (
                    <div className={cx('composer-code-change-slot')}>
                      <CodeChangeCard
                        codeChanges={pendingCodeChangeContext.changes}
                        compact
                        loading={loading}
                        onApproveAll={handleApprovePendingCodeChange}
                        onOpenFile={() => undefined}
                        onRevert={() => {
                          if (pendingCodeChangeContext.messageId === undefined) return
                          requestCodeChangeRevert(
                            pendingCodeChangeContext.messageId,
                            pendingCodeChangeContext.changes
                          )
                        }}
                        revertDisabled={
                          loading || workspaceBusy || versionReadOnly || viewingHistoricalStage
                        }
                        reverting={revertingCodeChangeIds.has(pendingCodeChangeContext.changes.id)}
                      />
                    </div>
                  ) : null}

                  <ChatComposer
                    activeWorkflow={activeWorkflow}
                    artifactResources={composerArtifactResources}
                    copy={copy}
                    draft={draft}
                    error={error}
                    loading={designSessionSwitching || loading}
                    onDraftChange={(value) => setDraftByKey(draftKey, value)}
                    onArtifactAttach={handleAttachArtifactToConversation}
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
              </div>
            )}
            {elementInspectionActive && (
              <div aria-hidden="true" className={cx('element-inspection-interaction-mask')} />
            )}
          </div>

          {showRightPanel && rightPanelLayout === 'split' && (
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
            />
          )}

          {activeView === 'chat' &&
            (!showRightPanel ||
              rightPanel?.type === 'preview' ||
              rightPanel?.type === 'doc' ||
              rightPanel?.type === 'process' ||
              rightPanel?.type === 'source') &&
            (rightPanelLayout === 'hidden' || Boolean(rightPanel)) && (
              <WorkbenchRightPanel
                tabs={workspaceTabs}
                activeTab={activeWorkspaceTab}
                layout={rightPanelLayout}
                onLayoutChange={handleRightPanelLayoutChange}
                onTabChange={openWorkspaceTab}
              >
                {rightPanel?.type === 'preview' && (
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
                    applicationMode={isAcceptancePreviewTab}
                    errorMessage={runtimePreviewLaunchError}
                    acceptanceEnabled={isAcceptancePreviewTab}
                    acceptanceAccepted={acceptanceAccepted}
                    onAcceptApplication={handleAcceptApplication}
                    acceptanceReadOnly={versionReadOnly}
                    onSubmitAcceptanceFeedback={handleSubmitAcceptanceFeedback}
                    onInspectingChange={setElementInspectionActive}
                  />
                )}
                {(rightPanel?.type === 'doc' || rightPanel?.type === 'source') && (
                  <SourcePanel
                    diff={pendingFileDiff ?? null}
                    docConfig={projectDocConfig}
                    directories={workspaceScaffoldDirectories}
                    files={workspaceSourceFiles}
                    initialFilePath={projectInitialFileExists ? projectInitialFilePath : ''}
                  />
                )}
              </WorkbenchRightPanel>
            )}

    </section>
  )
}

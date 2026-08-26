import { HolderOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, message } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench, useWorkbenchPhase } from '../../context'
import {
  hasApplicationEnteredDevelopment,
  markApplicationEnteredDevelopment
} from '../../workbenchPhase'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
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
import {
  planningWorkflowActivity,
  planningRequirementsConfirmed,
  ensureApplicationPlanningAction,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  shouldBackfillPlanningWorkflow
} from '../Welcome/planningWorkflowState'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import DesignChangeLockDock from './components/DesignChangeLockDock'
import DocPanel from './components/DocPanel'
import SourcePanel from './components/SourcePanel'
import UiDesignPreviewPanel from './components/UiDesignPreviewPanel'
import MessageList from './components/MessageList'
import PageContextHeader from './components/PageContextHeader'
import type { PageContextStatus } from './components/PageContextHeader'
import PlanExecutionDock from './components/PlanExecutionDock'
import RightPanelTabs, {
  type WorkspaceTab,
  type WorkspaceTabKey
} from './components/RightPanelTabs'
import SessionSidebar from './components/SessionSidebar'
import WorkspaceDebugDock from './components/WorkspaceDebugDock'
import EntityInfoPanel from './components/EntityInfoPanel'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
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
  requiresEntitySourceBinding,
  sessionDetailTargetKey,
  shouldShowEndpointDetailDesignEntry,
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
  planExecutionContextForRun,
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
  // 但“跳过”会推进到技术规划阶段，属于阶段切换，需要留痕。
  if ('ui_design_action' in answers) {
    const action = (answers as { ui_design_action?: { action?: string } }).ui_design_action
    return action?.action === 'skip' ? '跳过 UI 设计稿，直接进入技术规划' : ''
  }
  const lines: string[] = []
  for (const [key, value] of entries) {
    if (key === '__applicationPlanningAction') continue
    const text = planningAnswerToText(value)
    if (!text) continue
    const label = PLANNING_ANSWER_LABELS[key] || key
    lines.push(`${label}：${text}`)
  }
  return lines.join('\n')
}

const PLANNING_ANSWER_LABELS: Record<string, string> = {
  ui_design_confirmation: 'UI 设计稿确认',
  requirement_document_confirmation: '需求文档确认',
  requirement_spec_feedback: '需求文档意见',
  design_change_request: '设计变更',
  technical_plan_confirmation: '技术规划确认',
  project_plan_confirmation: '项目计划确认',
  entity_source_binding: '实体数据源绑定'
}

// 将设计阶段节点映射到右侧对应的规划文档标签。
// 需求与产品规划合并为同一个「需求文档」tab，两个阶段都映射到它。
const PHASE_DOC_KEY: Record<string, WorkspaceDocKey> = {
  requirements: 'requirement-spec',
  product_planning: 'requirement-spec',
  ui_confirmation: 'ui-design',
  technical_planning: 'technical-plan'
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
      const other =
        typeof record.other === 'string' && record.other.trim() ? `（${record.other}）` : ''
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
  developmentPlanningEntities: DevelopmentPlanningEntityOption[]
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
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => void
  onStopPlanning: () => Promise<void>
  onThemeChange: (theme: 'light' | 'dark') => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 设计阶段规划 Graph 的错误，来自仍在后台挂载的规划窗口。 */
  planningError?: string
  /** 从工作台错误卡片重新打开规划窗口，复用规划窗口内的重试动作。 */
  onRetryPlanning?: () => void
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
  | { type: 'entity'; entityId: string; label: string }

const ACTIVE_DESIGN_WORKFLOW_STATUSES = new Set([
  'running',
  'requires_user_input',
  'paused',
  'stopping'
])

type DesignDocArtifactKey = 'requirement-spec' | 'product-plan' | 'ui-design' | 'technical-plan'

type LocalDesignMarkdown = {
  content: string
  path: string
}

const TECHNICAL_PLAN_JSON_PATH = '.xcodeagent/plans/technical-plan.json'
const PRODUCT_PLAN_JSON_PATHS = [
  '.xcodeagent/drafts/plans/product-plan.json',
  '.xcodeagent/plans/product-plan.json'
] as const
const REQUIREMENT_SPEC_JSON_PATHS = [
  '.xcodeagent/drafts/specs/requirement-spec.json',
  '.xcodeagent/specs/requirement-spec.json'
] as const

const LOCAL_DESIGN_ARTIFACTS: ReadonlyArray<{
  key: DesignDocArtifactKey
  markdownPaths: readonly string[]
  contentPaths?: readonly string[]
}> = [
  {
    key: 'requirement-spec',
    markdownPaths: [
      '.xcodeagent/drafts/specs/requirement-spec.md',
      '.xcodeagent/specs/requirement-spec.md'
    ]
  },
  {
    key: 'product-plan',
    markdownPaths: ['.xcodeagent/drafts/plans/product-plan.md', '.xcodeagent/plans/product-plan.md']
  },
  {
    key: 'ui-design',
    markdownPaths: [],
    contentPaths: ['.xcodeagent/specs/ui-designs.json']
  },
  {
    key: 'technical-plan',
    markdownPaths: ['.xcodeagent/plans/technical-plan.md']
  }
]

const DESIGN_ARTIFACT_PATH_FIELDS: Record<DesignDocArtifactKey, readonly string[]> = {
  'requirement-spec': ['requirement_spec_path'],
  'product-plan': ['product_plan_path'],
  'ui-design': [],
  'technical-plan': ['technical_plan_path']
}

/** 把 Workflow 快照中的未知值安全收窄为普通对象。 */
function asWorkflowRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** 从单个 Workflow 投影对象中提取指定设计产物的 Markdown 路径。 */
function artifactPathFromRecord(value: unknown, key: DesignDocArtifactKey): string | undefined {
  const record = asWorkflowRecord(value)
  if (!record) return undefined
  for (const field of DESIGN_ARTIFACT_PATH_FIELDS[key]) {
    const path = record[field]
    if (typeof path === 'string' && path.trim()) return path
  }
  return undefined
}

/** 从节点完成事件中提取设计产物路径，作为写入成功后的刷新依据。 */
function artifactPathFromWorkflowEvent(
  event: WorkflowRunPayload['events'][number],
  key: DesignDocArtifactKey
): string | undefined {
  const data = asWorkflowRecord(event.data)
  if (!data) return undefined
  return (
    artifactPathFromRecord(data.artifacts, key) ||
    artifactPathFromRecord(data.stateDelta, key) ||
    artifactPathFromRecord(data.detail, key)
  )
}

/** 返回当前 Workflow 已知的设计产物路径，不再通过探测磁盘判断产物是否存在。 */
function workflowArtifactPath(
  workflow: WorkflowRunPayload | undefined,
  key: DesignDocArtifactKey
): string | undefined {
  if (!workflow) return undefined
  const currentSources: unknown[] = [
    workflow.summary?.artifacts,
    workflow.summary,
    workflow.state,
    workflow.result
  ]
  for (const source of currentSources) {
    const path = artifactPathFromRecord(source, key)
    if (path) return path
  }
  if (key !== 'ui-design' && workflow.confirmationArtifact?.id === key.replace('-', '_')) {
    return workflow.confirmationArtifact.path
  }
  for (let index = workflow.events.length - 1; index >= 0; index -= 1) {
    const path = artifactPathFromWorkflowEvent(workflow.events[index], key)
    if (path) return path
  }
  return undefined
}

/** 判断一个状态投影中是否已经携带 UI 设计稿页面。 */
function hasUiDesignPages(value: unknown): boolean {
  const record = asWorkflowRecord(value)
  if (!record) return false
  if (Array.isArray(record.pages) && record.pages.length > 0) return true
  const nested = asWorkflowRecord(record.ui_designs)
  return Boolean(nested && Array.isArray(nested.pages) && nested.pages.length > 0)
}

/** 根据 Workflow 状态或 UI 节点完成事件判断 UI 设计稿是否可用。 */
function workflowHasUiDesign(workflow: WorkflowRunPayload | undefined): boolean {
  if (!workflow) return false
  const currentSources: unknown[] = [
    workflow.state?.ui_designs,
    workflow.result?.ui_designs,
    workflow.summary?.clarification
  ]
  if (currentSources.some((source) => hasUiDesignPages(source))) return true
  return workflow.events.some(
    (event) => event.type === 'workflow.node.completed' && event.nodeName === 'ui_confirmation'
  )
}

/** 为已写入的设计产物生成稳定版本标记，避免 phase/status 快照重复触发读取。 */
function workflowDesignArtifactRevision(
  workflow: WorkflowRunPayload | undefined,
  key: DesignDocArtifactKey
): string {
  if (!workflow) return ''
  const currentPath = workflowArtifactPath(workflow, key)
  for (let index = workflow.events.length - 1; index >= 0; index -= 1) {
    const event = workflow.events[index]
    if (event.type !== 'workflow.node.completed') continue
    const eventPath = artifactPathFromWorkflowEvent(event, key)
    if (
      (key !== 'ui-design' && eventPath && (!currentPath || eventPath === currentPath)) ||
      (key === 'ui-design' && event.nodeName === 'ui_confirmation')
    ) {
      const sequence = asWorkflowRecord(event)?.sequence || index
      return `${workflow.runId}:${sequence}:${eventPath || event.nodeName || key}`
    }
  }
  if (currentPath) {
    const contentLength =
      key !== 'ui-design' && workflow.confirmationArtifact?.id === key.replace('-', '_')
        ? workflow.confirmationArtifact.content.length
        : 0
    return `snapshot:${workflow.runId}:${currentPath}:${contentLength}`
  }
  return workflowHasUiDesign(workflow) ? `snapshot:${workflow.runId}:${key}` : ''
}

/** 从工作区读取指定设计产物的可展示内容；未指定草稿状态时按候选路径取第一份非空内容。 */
async function readLocalDesignMarkdown(
  workspaceRoot: string,
  artifact: (typeof LOCAL_DESIGN_ARTIFACTS)[number],
  draftOnly?: boolean
): Promise<LocalDesignMarkdown> {
  const markdownPaths =
    artifact.contentPaths ||
    (draftOnly === undefined
      ? artifact.markdownPaths
      : draftOnly
        ? artifact.markdownPaths.filter((path) => path.includes('/drafts/'))
        : artifact.markdownPaths.filter((path) => !path.includes('/drafts/')))
  let lastError: unknown
  let foundFile = false
  let foundPath = ''
  for (const path of markdownPaths) {
    try {
      const result = await readWorkspaceFile({
        workspace_root: workspaceRoot,
        path,
        max_lines: 5000,
        max_chars: 200000
      })
      foundFile = true
      foundPath = path
      if (result.content.trim()) return { content: result.content, path }
    } catch (error) {
      lastError = error
    }
  }
  if (foundFile) return { content: '', path: foundPath }
  throw lastError || new Error('设计文档 Markdown 不存在')
}

/** 从当前工作区读取 TechnicalPlan 的内部结构化快照，供右侧可视化组件使用。 */
async function readLocalTechnicalPlanJson(
  workspaceRoot: string
): Promise<Record<string, unknown> | undefined> {
  try {
    const result = await readWorkspaceFile({
      workspace_root: workspaceRoot,
      path: TECHNICAL_PLAN_JSON_PATH,
      // file.read 工具当前契约上限为 5000 行、200000 字符；TechnicalPlan JSON 在此范围内。
      max_lines: 5000,
      max_chars: 200000
    })
    const parsed: unknown = JSON.parse(result.content)
    return asWorkflowRecord(parsed)
  } catch {
    // TechnicalPlan JSON 尚未生成或暂时不可读时，由右侧面板显示对应的空态。
    return undefined
  }
}

/** 从当前工作区读取 ProductPlan 的当前结构化快照，草稿优先、正式文档兜底。 */
async function readLocalProductPlanJson(
  workspaceRoot: string
): Promise<Record<string, unknown> | undefined> {
  for (const path of PRODUCT_PLAN_JSON_PATHS) {
    try {
      const result = await readWorkspaceFile({
        workspace_root: workspaceRoot,
        path,
        max_lines: 5000,
        max_chars: 200000
      })
      const parsed: unknown = JSON.parse(result.content)
      const record = asWorkflowRecord(parsed)
      if (record) return record
    } catch {
      // 候选路径不存在时尝试下一个；全部不可读时由面板显示对应的空态。
    }
  }
  return undefined
}

/** 从当前工作区读取 RequirementSpec 的结构化快照，草稿优先、正式文档兜底。 */
async function readLocalRequirementSpecJson(
  workspaceRoot: string
): Promise<Record<string, unknown> | undefined> {
  for (const path of REQUIREMENT_SPEC_JSON_PATHS) {
    try {
      const result = await readWorkspaceFile({
        workspace_root: workspaceRoot,
        path,
        max_lines: 5000,
        max_chars: 200000
      })
      const parsed: unknown = JSON.parse(result.content)
      const record = asWorkflowRecord(parsed)
      if (record) return record
    } catch {
      // 候选路径不存在时尝试下一个；全部不可读时由面板显示 Markdown 兜底。
    }
  }
  return undefined
}

/** 从本地 Markdown 或当前确认快照读取设计文档正文，保证 tab 的可用性以真实内容为准。 */
function designDocContentFor(
  localContents: Record<string, string>,
  workflow: WorkflowRunPayload | undefined,
  key: DesignDocArtifactKey
): string {
  const localContent = localContents[key] || ''
  if (localContent.trim()) return localContent

  const confirmationArtifact = workflow?.confirmationArtifact
  const expectedArtifactId =
    key === 'requirement-spec'
      ? 'requirement_spec'
      : key === 'product-plan'
        ? 'product_plan'
        : key === 'technical-plan'
          ? 'technical_plan'
          : undefined
  return confirmationArtifact && confirmationArtifact.id === expectedArtifactId
    ? confirmationArtifact.content
    : ''
}

/** 把第二份文档的首个 H1 降级为 H2 章节，供合并为一份连贯文档时接入。 */
function demoteLeadingH1(markdown: string): string {
  const lines = markdown.trim().split('\n')
  if (
    lines.length > 0 &&
    lines[0].trimStart().startsWith('# ') &&
    !lines[0].trimStart().startsWith('## ')
  ) {
    lines[0] = `## ${lines[0].trimStart().slice(2).trim()}`
  }
  return lines.join('\n')
}

/** 需求与产品规划合并展示：一份连贯的“需求文档”，需求在前、产品规划作为后续章节接入。 */
function mergedRequirementDocContentFor(
  localContents: Record<string, string>,
  workflow: WorkflowRunPayload | undefined
): string {
  // 产品规划确认门期间，后端 artifact 已是合并好的需求文档，直接使用避免重复拼接。
  const artifact = workflow?.confirmationArtifact
  if (artifact?.id === 'product_plan' && artifact.content.trim()) {
    return artifact.content
  }
  const requirementDoc = (localContents['requirement-spec'] || '').trimEnd()
  const productPlanDoc = (localContents['product-plan'] || '').trim()
  if (productPlanDoc) {
    const mergedPlan = demoteLeadingH1(productPlanDoc)
    return requirementDoc.trim() ? `${requirementDoc}\n\n${mergedPlan}` : mergedPlan
  }
  if (requirementDoc.trim()) return requirementDoc
  // 兼容旧工作区的独立需求确认门快照。
  return artifact?.id === 'requirement_spec' ? artifact.content : ''
}

/** 从当前规划 Workflow 读取 TechnicalPlan 结构化快照，供右侧只读产物视图使用。 */
function technicalPlanFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  if (!workflow) return undefined
  for (const source of [workflow.state, workflow.result]) {
    const value = source?.technical_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/** 从当前规划 Workflow 读取 ProductPlan 结构化快照，优先使用本轮最新页面名称。 */
function productPlanFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  if (!workflow) return undefined
  for (const source of [workflow.state, workflow.result]) {
    const value = source?.product_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/** 从当前规划 Workflow 读取 RequirementSpec 结构化快照，供右侧需求文档可视化视图使用。 */
function requirementSpecFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  if (!workflow) return undefined
  for (const source of [workflow.state, workflow.result]) {
    const value = source?.requirement_spec
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/** 为页面或接口生成稳定的前端目标键，隔离各目标的临时交互状态。 */
function detailTargetKey(target: ActiveDetailTarget): string {
  if (target.type === 'page') return pageDetailTargetKey(target.pageId)
  if (target.type === 'endpoint') {
    return endpointDetailTargetKey(target.apiContractId, target.endpointId)
  }
  if (target.type === 'entity') return `entity:${target.entityId}`
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
      (clarification as Record<string, unknown>).mode === 'entity_source_binding'
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
  targetType: 'page' | 'api' | 'entity',
  taskSummary?: DevelopmentPlanningPageOption['taskSummary']
): PageContextStatus {
  const targetLabel =
    targetType === 'entity' ? '实体设计' : targetType === 'api' ? 'API 设计' : '页面设计'
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
    mode === 'awaiting_unit_test_confirmation' ||
    mode === 'awaiting_test_phase_confirmation' ||
    mode === 'awaiting_review_phase_confirmation' ||
    mode === 'awaiting_code_review_repair_confirmation' ||
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
  developmentPlanningEntities,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  previewLaunchLoading,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStopPlanning,
  onThemeChange,
  onPlanningStreamReady,
  generatingTemplate,
  planningError,
  onRetryPlanning,
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
  // 设计阶段自由变更是主规划 Workflow 的显式中断模式，默认保持锁定。
  const [designChangeUnlocked, setDesignChangeUnlocked] = useState(false)
  // 按页面/API目标保留输入模式，切换会话时不让用户反复选择同一模式。
  const [inputModes, setInputModes] = useState<Record<string, ChatInputMode>>({})
  const [interactingDetailTargetKey, setInteractingDetailTargetKey] = useState('')
  const [generatingDetailTargetKey, setGeneratingDetailTargetKey] = useState('')
  // 仅在用户点击页面开始设计后记录本次工作台内的页面设计状态。
  const [startedPageDesignIds, setStartedPageDesignIds] = useState<Set<string>>(() => new Set())
  const [previewError, setPreviewError] = useState('')
  const [elementInspectionActive, setElementInspectionActive] = useState(false)
  // UI 设计稿预览：右侧"UI设计稿"tab 当前选中的页面 id（由中间区卡片或右侧列表驱动）。
  const [uiDesignActivePageId, setUiDesignActivePageId] = useState('')
  // UI 设计稿：当前正在执行动作（选模板/换一换）的 pageId 集合，用于右侧预览逐页显示加载态。
  const [uiDesignActingPageIds, setUiDesignActingPageIds] = useState<string[]>([])
  // 设计阶段右侧文档：仅缓存用户实际打开过的 Markdown；确认完成后按需从磁盘读取，避免右侧 tab 显示待生成。
  const [designDocFileContent, setDesignDocFileContent] = useState<Record<string, string>>({})
  const [designDocFilePath, setDesignDocFilePath] = useState<Record<string, string>>({})
  // TechnicalPlan 右侧展示使用内部 JSON，Markdown 仅作为正式用户工件保留。
  const [technicalPlanFile, setTechnicalPlanFile] = useState<Record<string, unknown>>()
  const [productPlanFile, setProductPlanFile] = useState<Record<string, unknown>>()
  const [requirementSpecFile, setRequirementSpecFile] = useState<Record<string, unknown>>()
  const [technicalPlanFileLoading, setTechnicalPlanFileLoading] = useState(false)
  const designDocCacheRevisionRef = useRef<Record<string, string>>({})
  const [designDocLoadingKey, setDesignDocLoadingKey] = useState<WorkspaceDocKey>()
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  // 已处理过完成跳转的实体设计运行，避免打开历史会话时再次跳回信息面板。
  const handledEntityDesignRunRef = useRef<Set<string>>(new Set())
  // 标记当前实体会话是否在本应用会话内真实运行过（经历过 loading）。
  // 历史会话恢复的最后一条消息也是已完成快照，不能据其触发跳回信息面板。
  const entityRunWasLiveRef = useRef(false)
  // 实体设计整轮结束后正在跳回信息面板：刷新期间实体仍显示未设计，
  // 用该标记抑制引导卡片闪现，直到大纲状态刷新为已设计。
  const [entityDesignReturning, setEntityDesignReturning] = useState(false)
  const { publishAiMessage } = useWorkbench()
  const { phase: activeWorkbenchPhase, switchPhase } = useWorkbenchPhase()
  const isDesignPhase = activeWorkbenchPhase === 'product'

  // 切换应用或规划线程时回到主流程锁定态，避免把上一个规划的自由变更模式带入新会话。
  useEffect(() => {
    setDesignChangeUnlocked(false)
  }, [application.id, isDesignPhase, planningThreadId])
  // 模板生成完成后（lifecycle 变为 ready_for_workbench），derivedPhase 自动变 development。
  // 前端拦截：保持 product 阶段，等用户点"进入开发"按钮后才放开（switchPhase(null) 恢复跟随旅程）。
  // 用 sessionStorage 按 applicationId 记录用户是否已确认进入开发，跨重挂载保持。
  // 关键：必须检查 lifecycle.initialization.stage === 'ready_for_workbench'，而非仅 derivedPhase=development。
  // 因为 deriveWorkbenchPhase 在 lifecycle 未加载时也默认返回 'development'（workbenchPhase.ts:114），
  // 仅凭 derivedPhase 会误触发拦截，导致后续真正完成时 ref 已置位、gate 不再出现。
  const lifecycleReadyForWorkbench =
    applicationLifecycle?.initialization?.stage === 'ready_for_workbench'
  const [enterDevConfirmed, setEnterDevConfirmed] = useState(() =>
    hasApplicationEnteredDevelopment(application.id)
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
  const handleUiDesignActivePageChange = useCallback(
    (pageId: string) => {
      setUiDesignActivePageId(pageId)
      if (rightPanel?.type !== 'doc' || rightPanel.docKey !== 'ui-design') {
        setRightPanel({ type: 'doc', docKey: 'ui-design' })
      }
    },
    [rightPanel, setRightPanel]
  )
  // 右侧面板实际展示：外部开关 + 面板有内容。开关由 WorkbenchPage 顶栏控制，
  // 面板内容（preview/doc/diff）由本组件按目标类型设置。
  const showRightPanel = rightPanelOpen && Boolean(rightPanel)

  // ---- 设计阶段（product）：右侧展示需求文档/产品规划/UI设计稿/技术规划 ----
  // 需求文档在模型生成后即可展示；确认状态只决定它是草稿还是正式文档。
  // UI 设计稿：从规划 workflow 的 clarification（ui_design_confirmation 模式）或
  // state/result 的 ui_designs 读取页面列表。设计稿生成中或已就绪都算可用。
  const planningClarification = planningWorkflow?.summary?.clarification as
    | { mode?: string; status?: string; pages?: unknown[] }
    | undefined
  const planningPhaseRunning = planningWorkflow?.summary?.status === 'running'
  const planningPhase = planningWorkflow?.summary?.phase
  const requirementSpecPath =
    workflowArtifactPath(planningWorkflow, 'requirement-spec') ||
    designDocFilePath['requirement-spec']
  const requirementsConfirmed = planningRequirementsConfirmed(planningWorkflow, requirementSpecPath)
  const planningUiDesignPagesSource: unknown[] | undefined =
    Array.isArray(planningClarification?.pages) && planningClarification!.pages!.length > 0
      ? planningClarification!.pages
      : ((planningWorkflow?.state?.ui_designs as { pages?: unknown[] } | undefined)?.pages ??
        (planningWorkflow?.result?.ui_designs as { pages?: unknown[] | undefined } | undefined)
          ?.pages)
  // UI 设计稿页面列表（右侧"UI设计稿"tab 预览用）。
  // workflow running 期间流式快照可能丢失 page.code，用 ref 缓存上一次有 code 的 pages，
  // running 期间回退到缓存，避免右侧设计稿闪烁消失、tab 被误禁用。
  const uiDesignPagesCacheRef = useRef<
    Array<{ pageId?: string; name?: string; code?: string; status?: string; template_id?: string }>
  >([])
  const uiDesignPages = useMemo(() => {
    const raw = (
      Array.isArray(planningUiDesignPagesSource)
        ? planningUiDesignPagesSource.filter((p) => p && typeof p === 'object')
        : []
    ) as Array<{
      pageId?: string
      name?: string
      code?: string
      status?: string
      template_id?: string
    }>
    if (raw.some((p) => Boolean(p.code))) {
      uiDesignPagesCacheRef.current = raw
      return raw
    }
    if (planningPhaseRunning && uiDesignPagesCacheRef.current.length > 0) {
      return uiDesignPagesCacheRef.current
    }
    return raw
  }, [planningUiDesignPagesSource, planningPhaseRunning])
  const requirementDocContent = mergedRequirementDocContentFor(
    designDocFileContent,
    planningWorkflow
  )
  const technicalPlanDocContent = designDocContentFor(
    designDocFileContent,
    planningWorkflow,
    'technical-plan'
  )
  const uiDesignDocContent = designDocFileContent['ui-design'] || ''
  const requirementDocAvailable = Boolean(requirementDocContent.trim())
  const uiDesignAvailable = Boolean(uiDesignDocContent.trim()) || uiDesignPages.length > 0
  const technicalPlanDocAvailable = Boolean(technicalPlanDocContent.trim())
  // 版本标记变化时重新同步磁盘内容；应用重新打开且没有 Workflow 快照时仍会执行首次读取。
  const designArtifactLoadRevision = LOCAL_DESIGN_ARTIFACTS.map(
    (artifact) =>
      `${artifact.key}:${workflowDesignArtifactRevision(planningWorkflow, artifact.key)}`
  ).join('|')
  const designDocs = isDesignPhase
    ? ([
        {
          key: 'requirement-spec' as WorkspaceDocKey,
          title: requirementsConfirmed ? '需求文档' : '需求文档（草稿）',
          path: 'specs/requirement-spec.md',
          content: requirementDocContent,
          available: requirementDocAvailable
        },
        {
          key: 'technical-plan' as WorkspaceDocKey,
          title: '技术规划',
          path: 'plans/technical-plan.json',
          content: technicalPlanDocContent,
          available: technicalPlanDocAvailable
        },
        {
          key: 'ui-design' as WorkspaceDocKey,
          title: 'UI设计稿',
          path: 'specs/ui-designs',
          content: uiDesignDocContent,
          available: uiDesignAvailable
        }
      ] as Array<{
        key: WorkspaceDocKey
        title: string
        path: string
        content: string
        available: boolean
      }>)
    : undefined
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
  const activeDesignDoc = designDocs?.find((doc) => doc.key === activeDesignDocKey)
  const activeDesignArtifact = LOCAL_DESIGN_ARTIFACTS.find(
    (artifact) => artifact.key === activeDesignDocKey
  )
  const activeDesignArtifactRevision = activeDesignArtifact
    ? workflowDesignArtifactRevision(planningWorkflow, activeDesignArtifact.key)
    : ''
  const activeDesignDocAvailable = Boolean(activeDesignDoc?.available)
  const technicalPlanViewActive = isDesignPhase && activeDesignDocKey === 'technical-plan'
  const requirementDocViewActive = isDesignPhase && activeDesignDocKey === 'requirement-spec'
  // 右侧技术规划始终走结构化视图；运行中优先使用 Workflow，重开工作区时读取正式 JSON。
  const technicalPlanForDoc = technicalPlanViewActive
    ? technicalPlanFromWorkflow(planningWorkflow) || technicalPlanFile
    : undefined
  const productPlanForDoc = technicalPlanViewActive
    ? productPlanFromWorkflow(planningWorkflow) || productPlanFile
    : undefined
  const technicalPlanViewLoading =
    technicalPlanViewActive &&
    (!technicalPlanForDoc || !productPlanForDoc) &&
    technicalPlanFileLoading
  // 需求文档 tab 优先使用结构化可视化；结构化数据不可读时回退 Markdown。
  const requirementSpecForDoc = requirementDocViewActive
    ? requirementSpecFromWorkflow(planningWorkflow) || requirementSpecFile
    : undefined
  const requirementProductPlanForDoc = requirementDocViewActive
    ? productPlanFromWorkflow(planningWorkflow) || productPlanFile
    : undefined

  // 进入工作台时一次性读取四类本地设计产物，重新打开应用也不依赖内存 Workflow 快照。
  useEffect(() => {
    const workspaceRoot = application.workspaceRoot
    if (!isDesignPhase || !workspaceRoot) {
      setTechnicalPlanFile(undefined)
      setProductPlanFile(undefined)
      setRequirementSpecFile(undefined)
      setTechnicalPlanFileLoading(false)
      return
    }
    let cancelled = false
    setTechnicalPlanFileLoading(true)

    const loadDesignDocuments = async (): Promise<void> => {
      const [entries, localTechnicalPlan, localProductPlan, localRequirementSpec] =
        await Promise.all([
          Promise.all(
            LOCAL_DESIGN_ARTIFACTS.map(async (artifact) => {
              try {
                const document = await readLocalDesignMarkdown(workspaceRoot, artifact)
                return document.content.trim() ? ([artifact.key, document] as const) : null
              } catch {
                // 工作区没有对应产物属于正常空态，不影响其他文档继续加载。
                return null
              }
            })
          ),
          readLocalTechnicalPlanJson(workspaceRoot),
          readLocalProductPlanJson(workspaceRoot),
          readLocalRequirementSpecJson(workspaceRoot)
        ])
      if (cancelled) return
      const nextContents = Object.fromEntries(
        entries
          .filter(
            (entry): entry is readonly [DesignDocArtifactKey, LocalDesignMarkdown] => entry !== null
          )
          .map(([key, document]) => [key, document.content])
      )
      const nextPaths = Object.fromEntries(
        entries
          .filter(
            (entry): entry is readonly [DesignDocArtifactKey, LocalDesignMarkdown] => entry !== null
          )
          .map(([key, document]) => [key, document.path])
      )
      setDesignDocFileContent(nextContents)
      setDesignDocFilePath(nextPaths)
      setTechnicalPlanFile(localTechnicalPlan)
      setProductPlanFile(localProductPlan)
      setRequirementSpecFile(localRequirementSpec)
      setTechnicalPlanFileLoading(false)
    }

    void loadDesignDocuments()
    return () => {
      cancelled = true
      setTechnicalPlanFileLoading(false)
    }
  }, [
    application.id,
    application.workspaceRoot,
    designArtifactLoadRevision,
    isDesignPhase,
    planningPhase,
    planningWorkflow?.summary?.status
  ])

  // 首次进入已完成全量读取；用户打开某个 tab 后按 Workflow 版本补读对应 Markdown。
  // 合并后的「需求文档」tab 内容由需求与产品规划两份产物拼成，激活时两份都要补读，
  // 否则全量加载错过时机后产品规划部分会永远缺失，tab 退化为只显示需求内容。
  useEffect(() => {
    if (
      !isDesignPhase ||
      !application.workspaceRoot ||
      !activeDesignArtifact ||
      !activeDesignDocAvailable ||
      activeDesignArtifact.key === 'ui-design'
    ) {
      return
    }
    const artifactsToRead =
      activeDesignArtifact.key === 'requirement-spec'
        ? LOCAL_DESIGN_ARTIFACTS.filter(
            (artifact) => artifact.key === 'requirement-spec' || artifact.key === 'product-plan'
          )
        : [activeDesignArtifact]
    const key = activeDesignArtifact.key
    const revision = activeDesignArtifactRevision || `available:${key}`
    // 合并 tab 的缓存键必须同时包含产品规划版本：产品规划晚于需求生成/晋升时，
    // 仅需求版本不变也不能跳过重读，否则产品规划部分永远不会补进 tab。
    const mergedRevision =
      activeDesignArtifact.key === 'requirement-spec'
        ? `${revision}|product:${workflowDesignArtifactRevision(planningWorkflow, 'product-plan') || 'pending'}`
        : revision
    const cacheKey = `${application.workspaceRoot}:${key}:${mergedRevision}`
    if (designDocCacheRevisionRef.current[key] === cacheKey) return

    setDesignDocLoadingKey(key)
    void Promise.all(
      artifactsToRead.map(async (artifact) => {
        try {
          const document = await readLocalDesignMarkdown(
            application.workspaceRoot as string,
            artifact,
            artifact.key === 'requirement-spec' && !requirementsConfirmed
          )
          return [artifact.key, document] as const
        } catch {
          // 对应产物尚未生成时保持空态，由后续 revision 变化再次补读。
          return null
        }
      })
    )
      .then((documents) => {
        const readable = documents.filter(
          (entry): entry is readonly [DesignDocArtifactKey, LocalDesignMarkdown] =>
            entry !== null && Boolean(entry[1].content.trim())
        )
        if (readable.length === 0) return
        setDesignDocFileContent((current) => ({
          ...current,
          ...Object.fromEntries(readable.map(([docKey, document]) => [docKey, document.content]))
        }))
        setDesignDocFilePath((current) => ({
          ...current,
          ...Object.fromEntries(readable.map(([docKey, document]) => [docKey, document.path]))
        }))
        designDocCacheRevisionRef.current[key] = cacheKey
      })
      .catch(() => {
        // 写入成功但 Markdown 读取失败时保留空态，下一次打开该 tab 会再次尝试。
      })
      .finally(() => {
        setDesignDocLoadingKey((current) => (current === key ? undefined : current))
      })
  }, [
    isDesignPhase,
    application.workspaceRoot,
    activeDesignArtifact,
    activeDesignDocAvailable,
    activeDesignArtifactRevision,
    planningWorkflow,
    requirementsConfirmed
  ])
  // 开发阶段：右侧文档区无设计阶段产物，显示引导文案（选中页面/端点后由后续逻辑填充）。
  const designDocContent = isDesignPhase
    ? activeDesignDoc?.content || ''
    : '从左侧大纲选择页面或接口，查看设计文档。'
  const designDocName = isDesignPhase ? activeDesignDoc?.title : undefined
  const designDocTitle = isDesignPhase
    ? activeDesignDoc?.key === 'requirement-spec' && !requirementsConfirmed
      ? `${activeDesignDoc?.path || ''} · 草稿`
      : activeDesignDoc?.path
    : undefined
  const uiDesignActivePage =
    uiDesignPages.find((p) => (p.pageId || '') === uiDesignActivePageId) || uiDesignPages[0]
  const uiDesignActivePageCode = uiDesignActivePage?.code || ''
  // 设计阶段文档生成中：规划 workflow 正在 running 且对应阶段。
  // 文档加载态按当前 tab 区分：合并后的需求文档 tab 在需求分析与产品规划两个
  // 阶段都显示生成中，技术规划 tab 只在对应阶段生成中，避免其他文档误显示加载态。
  const designDocGenerating =
    isDesignPhase &&
    planningPhaseRunning &&
    !activeDesignDoc?.available &&
    ((activeDesignDocKey === 'requirement-spec' &&
      (planningPhase === 'requirements' || planningPhase === 'product_planning')) ||
      (activeDesignDocKey === 'technical-plan' && planningPhase === 'technical_planning'))
  const designDocLoading =
    isDesignPhase &&
    activeDesignDocKey !== 'ui-design' &&
    activeDesignDocAvailable &&
    designDocLoadingKey === activeDesignDocKey
  // UI 设计稿生成中：UI 确认阶段 workflow running（换一换/选模板/首次生成）。
  const uiDesignGenerating =
    isDesignPhase && planningPhaseRunning && planningPhase === 'ui_confirmation'
  // acting 态的清理由 UiDesignConfirmationPanel 的 cleanup-effect（带 observedRunningRef
  // 防提前重置）全权管理；这里不再重复清理，避免与 panel 抢着清空导致下一批 acting 态
  // 在 flush 瞬间被清掉（按钮提前解禁、右侧 loading 消失）。
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
  const generatingDesignDocKey = planningPhaseRunning
    ? PHASE_DOC_KEY[String(planningPhase || '')]
    : undefined
  const workspaceTabs: WorkspaceTab[] = isDesignPhase
    ? (designDocs || []).map((doc) => ({
        key: doc.key,
        label: doc.title,
        // 当前阶段的文档即使尚未落盘也允许切回，内容区继续展示对应 loading。
        available: doc.available || doc.key === generatingDesignDocKey
      }))
    : [
        { key: 'preview', label: '预览', available: Boolean(runtimePreviewBaseUrl) },
        { key: 'source', label: '源码', available: Boolean(activePageOption) },
        { key: 'doc', label: '文档', available: true }
      ]
  const activeWorkspaceTab: WorkspaceTabKey = isDesignPhase
    ? activeDesignDocKey || designDocs?.find((doc) => doc.available)?.key || 'requirement-spec'
    : rightPanel?.type === 'preview'
      ? 'preview'
      : rightPanel?.type === 'source'
        ? 'source'
        : 'doc'
  const openWorkspaceTab = useCallback(
    (key: WorkspaceTabKey) => {
      if (isDesignPhase) {
        // 已生成文档与当前生成中的文档均可切换；其他未开始的文档继续保持禁用。
        const target = designDocs?.find((doc) => doc.key === key)
        if (!target || (!target.available && target.key !== generatingDesignDocKey)) return
        setRightPanel({ type: 'doc', docKey: key as WorkspaceDocKey })
      } else if (key === 'preview') {
        setRightPanel({ type: 'preview' })
      } else if (key === 'source') {
        setRightPanel({ type: 'source' })
      } else if (key === 'doc') {
        setRightPanel({ type: 'doc' })
      }
    },
    [designDocs, generatingDesignDocKey, isDesignPhase, setRightPanel]
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
    // 已选中的文档可能正处于生成阶段；保留该选择，避免首个可用文档覆盖阶段自动切换。
    if (rightPanel?.type === 'doc' || rightPanel?.type === 'preview') return
    const firstAvailable = designDocs?.find((doc) => doc.available)
    if (firstAvailable) {
      setRightPanel({ type: 'doc', docKey: firstAvailable.key })
    } else {
      setRightPanel({ type: 'doc', docKey: 'requirement-spec' })
    }
  }, [isDesignPhase, rightPanelOpen, rightPanel, designDocs, setRightPanel])

  // 需求文档确认阶段：需求文档（含合并的产品规划）生成后自动切到"需求文档"tab 展示内容。
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    const mode = planningClarification?.mode
    if (mode !== 'requirement_document_confirmation') return
    if (!requirementDocAvailable) return
    if (rightPanel?.type === 'doc' && rightPanel.docKey === 'requirement-spec') return
    setRightPanel({ type: 'doc', docKey: 'requirement-spec' })
  }, [
    isDesignPhase,
    rightPanelOpen,
    planningClarification,
    requirementDocAvailable,
    rightPanel,
    setRightPanel
  ])

  // 设计阶段右侧 tab 跟随当前规划阶段自动切换：阶段变化时切到对应文档 tab，
  // 同阶段内用户手动切到其他 tab 不被拉回。用 ref 记录上次自动同步的阶段，
  // 仅在 planningPhase 真正变化时触发，避免覆盖用户的同阶段内手动切换。
  const lastAutoSyncedPhaseRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!isDesignPhase || !rightPanelOpen) return
    const phase = planningPhase
    const docKey = phase ? PHASE_DOC_KEY[phase] : undefined
    if (!docKey) return
    // UI 设计稿 tab 需等设计稿生成后才可用，未就绪时不切到灰显 tab。
    if (phase === 'ui_confirmation' && !uiDesignAvailable) return
    // 仅在阶段切换时自动切 tab；同阶段内不干预用户的手动选择。
    if (lastAutoSyncedPhaseRef.current === phase) return
    lastAutoSyncedPhaseRef.current = phase
    if (rightPanel?.type === 'doc' && rightPanel.docKey === docKey) return
    setRightPanel({ type: 'doc', docKey })
  }, [isDesignPhase, rightPanelOpen, planningPhase, uiDesignAvailable, rightPanel, setRightPanel])

  // 切换应用时重置自动同步标记并清空右侧面板，避免上次会话选中的 tab 残留。
  useEffect(() => {
    lastAutoSyncedPhaseRef.current = undefined
    if (isDesignPhase) {
      setRightPanel(undefined)
    }
  }, [application.id, isDesignPhase, setRightPanel])

  // 切换工作区时清空上一应用的本地文档缓存，防止旧内容误放行 tab。
  useEffect(() => {
    setDesignDocFileContent({})
    setDesignDocFilePath({})
    designDocCacheRevisionRef.current = {}
    setDesignDocLoadingKey(undefined)
  }, [application.id, application.workspaceRoot])

  const activeApiEndpoint = activeDetailTarget.type === 'endpoint' ? activeDetailTarget : undefined
  const activeTargetKey = detailTargetKey(activeDetailTarget)
  const inputModeKey = activeTargetKey || 'free-chat'
  const inputMode: ChatInputMode =
    inputModes[inputModeKey] || (activeDetailTarget.type === 'none' ? 'conversation' : 'design')
  const planningWorkflowStatus = String(planningWorkflow?.summary?.status || '')
  // 模板就绪后创建规划已经结束；即使界面暂留在产品阶段等待“进入开发”，底部也应恢复普通自由对话。
  const designChangeWorkflowAvailable = isDesignPhase && !lifecycleReadyForWorkbench
  const designWorkflowActive =
    designChangeWorkflowAvailable &&
    (!planningWorkflow || ACTIVE_DESIGN_WORKFLOW_STATUSES.has(planningWorkflowStatus))
  const designChangeInputLocked = designWorkflowActive && !designChangeUnlocked
  const designChangeInputEnabled = isDesignPhase && !designChangeInputLocked
  const effectiveInputMode: ChatInputMode = designChangeInputEnabled ? 'conversation' : inputMode
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

  /** 测试会话启动时立即高亮测试阶段，避免生命周期回传延迟造成步骤条仍显示开发中。 */
  const handleEnterTestPhase = useCallback((): void => {
    switchPhase('test')
  }, [switchPhase])

  /** 审查会话创建成功后立即高亮审查阶段，避免等待代码扫描首帧才更新顶部步骤条。 */
  const handleEnterReviewPhase = useCallback((): void => {
    switchPhase('review')
  }, [switchPhase])

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
    createTestSession,
    createReviewSession,
    clearActiveSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensureEntitySession,
    ensurePageSession,
    ensurePlanningSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleSelectEndpoint,
    handleSelectEntity,
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
    setSessionMessages
  } = useChatSessions({
    application,
    editorMode,
    workbenchPhase: activeWorkbenchPhase,
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
    handleStartEndpointDevelopment,
    handleStartEntityDetailConfirmation,
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
    createTestSession,
    createReviewSession,
    ensureActiveSession,
    ensureEndpointSession,
    ensureEntitySession,
    ensurePageSession,
    getSessionMessages,
    persistSession,
    onApplicationLifecycleChange,
    onEnterTestPhase: handleEnterTestPhase,
    onEnterReviewPhase: handleEnterReviewPhase,
    onPreviewReady: handlePreviewReady,
    publishAiMessage,
    runningSessionsRef,
    selectedApiContractId: activeApiEndpoint?.apiContractId,
    selectedEndpointId: activeApiEndpoint?.endpointId,
    selectedEndpointLabel: activeApiEndpoint?.label,
    selectedEntityId:
      activeDetailTarget.type === 'entity' ? activeDetailTarget.entityId : undefined,
    selectedEntityLabel:
      activeDetailTarget.type === 'entity' ? activeDetailTarget.label : undefined,
    selectedSkills,
    selectedPageId: activePageOption?.pageId || activePageOption?.key,
    selectedPageLabel: activePageOption?.label,
    conversationEnabled,
    inputMode: effectiveInputMode,
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
  // 保存最新规划权威快照，供会话键晚于流式事件就绪时补齐最终确认卡。
  const planningWorkflowRef = useRef(planningWorkflow)
  planningWorkflowRef.current = planningWorkflow
  // sessionKey 就绪前缓存的流式 chunk，就绪后回放，避免最早的规划消息丢失。
  const pendingPlanningChunksRef = useRef<
    Array<{ content?: string; workflow?: WorkflowRunPayload }>
  >([])
  // onPlanningStreamReady 注册的注入句柄，保存需求文档草稿后用它把更新后的
  // workflow 注入回规划会话，驱动右侧需求文档 tab 实时刷新编辑后的内容。
  const planningStreamInjectRef = useRef<
    ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  >(null)
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
      if (!identity || identity.key !== sessionKey) return
      const msgs = getSessionMessagesRef.current(sessionKey)
      if (!msgs.length) return
      void persistSessionRef
        .current({
          editorMode: identity.editorMode,
          messages: msgs,
          sessionId: identity.sessionId,
          threadId: identity.threadId,
          apiContractId: identity.apiContractId,
          endpointId: identity.endpointId,
          endpointLabel: identity.endpointLabel,
          pageId: identity.pageId,
          titleFrom: '产品 Agent'
        })
        .catch((error) => console.warn('持久化规划会话失败。', error))
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
    const userMessageId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
    const assistantPlaceholderId = userMessageId + 1
    setSessionMessagesRef.current(sessionKey, (prev) => [
      ...prev,
      ...(text
        ? [
            {
              id: userMessageId,
              role: 'user' as const,
              content: text,
              createdAt: userMessageId
            }
          ]
        : []),
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
  const injectPlanningChunk = (
    sessionKey: string,
    chunk: { content?: string; workflow?: WorkflowRunPayload }
  ): void => {
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
    // 设计阶段的确认卡（需求/产品/技术/UI）在轮询、单页动作或无操作 resume 时会触发
    // 新 runId（每次 resume 都 randomUUID），且中途流式快照可能丢失 clarification.mode。
    // 只要最后一条消息与本次快照是同一种确认卡、且未显式开启新轮，就视为同轮更新，
    // 更新同一张卡片而非新增——否则每次轮询都会新建一张同 phase 的确认卡并残留 loading。
    const DESIGN_CONFIRMATION_MODES = new Set([
      'requirement_document_confirmation',
      'technical_plan_confirmation',
      'ui_design_confirmation'
    ])
    const lastClarificationMode = lastMessage?.workflow?.summary?.clarification as
      | { mode?: string }
      | undefined
    const chunkClarificationMode = chunk.workflow?.summary?.clarification as
      | { mode?: string }
      | undefined
    const lastMode = lastClarificationMode?.mode
    const chunkMode = chunkClarificationMode?.mode
    const sameUiDesignConfirmation =
      !planningNewRoundRef.current &&
      lastMessage?.role === 'assistant' &&
      Boolean(lastMode) &&
      DESIGN_CONFIRMATION_MODES.has(String(lastMode)) &&
      // 中途流式快照可能丢失 mode（undefined），此时沿用最后一张确认卡的判定；
      // 只有 chunk 明确带了不同 mode 才不算同轮。
      (!chunkMode || chunkMode === lastMode)

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
        // 判断该 chunk 是否已经携带可见状态。首次创建沿用原有阶段判定；
        // 只有设计变更轮次才由聊天活动块承接 requirements/UI 等实时进度。
        const chunkSettlesLoading = planningWorkflowSettlesLoading(chunk.workflow)
        const chunkHasContent = Boolean(chunk.content?.trim())
        const chunkActivity = planningWorkflowActivity(chunk.workflow)
        const chunkIsPlanningRunning =
          chunk.workflow.summary?.status === 'running' &&
          (['product_planning', 'project_planning', 'technical_planning'].includes(
            String(chunk.workflow.summary?.phase || '')
          ) ||
            chunkActivity?.status === 'running')
        const hasSubstantiveWorkflow =
          chunkSettlesLoading || chunkHasContent || chunkIsPlanningRunning
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
        const requiresInput = planningWorkflowRequiresUserInput(chunk.workflow)
        const chunkActivity = planningWorkflowActivity(chunk.workflow)
        const isPlanningRunning =
          chunk.workflow.summary?.status === 'running' &&
          (['product_planning', 'project_planning', 'technical_planning'].includes(
            String(chunk.workflow.summary?.phase || '')
          ) ||
            chunkActivity?.status === 'running')
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
        planningNewRoundRef.current && lastMessage?.role === 'assistant' && !lastWorkflowRunId
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
      const stable = planningWorkflowSettlesLoading(chunk.workflow)
      if (stable) {
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
    void ensurePlanningSessionRef.current(planningThreadId).then((identity) => {
      if (cancelled) return
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
      if (currentMsgs.length === 0) {
        const placeholderId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
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
      // 工作台或会话键晚于最终 AG-UI 帧就绪时，用外层保存的权威快照收口占位消息。
      const latestPlanningWorkflow = planningWorkflowRef.current
      if (shouldBackfillPlanningWorkflow(latestPlanningWorkflow, planningNewRoundRef.current)) {
        injectPlanningChunk(identity.key, { workflow: latestPlanningWorkflow })
      }
    })
    return () => {
      cancelled = true
    }
    // 只依赖 isDesignPhase/planningThreadId/loadingSessions，ensurePlanningSession 用 ref 避免循环。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDesignPhase, planningThreadId, loadingSessions])

  // 外层规划状态始终保留最新 Workflow；稳定快照到达时主动补齐可能漏掉的流式确认卡。
  useEffect(() => {
    const sessionKey = planningSessionKeyRef.current
    if (
      !isDesignPhase ||
      !planningThreadId ||
      !sessionKey ||
      !shouldBackfillPlanningWorkflow(planningWorkflow, planningNewRoundRef.current)
    ) {
      return
    }
    injectPlanningChunk(sessionKey, { workflow: planningWorkflow })
    // injectPlanningChunk 读取的会话操作均由 ref 保持最新，避免把函数身份加入依赖造成重复注入。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDesignPhase, planningThreadId, planningWorkflow])

  useEffect(() => {
    if (!onPlanningStreamReady) return
    // 不依赖 isDesignPhase：工作台刚进入时 lifecycle 尚未加载，isDesignPhase 为 false，
    // 若此时不注册句柄，Modal 最早的流式数据（"正在生成需求文档大纲…"）会被丢弃。
    // 总是注册，chunk 到达时 sessionKey 未就绪则缓存，待 ensurePlanningSession 完成后回放。
    const injectChunk = (chunk: { content?: string; workflow?: WorkflowRunPayload }): void => {
      const sessionKey = planningSessionKeyRef.current
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
  // 实体数据源绑定以聊天样式呈现，并作为独立流程结束。
  const entityDesignChatActive = Boolean(
    activeDetailTarget.type === 'entity' &&
      (!activeWorkflow || String(activeWorkflow.summary.phase || '') === 'entity_source_binding')
  )
  const targetExecutionContext = activeApiEndpoint
    ? planExecutionContextForEndpoint(
        applicationLifecycle,
        activeApiEndpoint.apiContractId,
        activeApiEndpoint.endpointId,
        workflowIdentity
      )
    : activeDetailTarget.type === 'entity'
      ? entityDesignChatActive
        ? // EntitySourceBinding 不归属页面/应用级执行上下文；
          // 置空执行态可避免应用级 execution 把计划模式置为非空闲，
          // 从而抑制未设计实体与页面/接口一致的锁定引导卡片。
          { execution: undefined, dependencyLocked: false }
        : // 实体设计确认后进入构建：只按当前 Workflow 自身执行定位，
          // 不再回退到应用级 execution，避免旧执行状态污染构建阶段 UI。
          planExecutionContextForRun(applicationLifecycle, workflowIdentity)
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
  const activeEntityOption = useMemo(() => {
    if (activeDetailTarget.type !== 'entity') return undefined
    return (
      developmentPlanningEntities.find((entity) => entity.id === activeDetailTarget.entityId) || {
        id: activeDetailTarget.entityId,
        label: activeDetailTarget.label,
        purpose: '',
        dataSourceType: '',
        designed: false,
        hasDetailPlan: false
      }
    )
  }, [activeDetailTarget, developmentPlanningEntities])
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
  const activeHeaderTarget =
    activeDetailTarget.type === 'entity'
      ? {
          type: 'entity' as const,
          title: activeDetailTarget.label,
          path: activeDetailTarget.entityId,
          description:
            activeEntityOption?.purpose || activeDetailTarget.label || '当前实体详细设计',
          keyFeatures: [
            `实体 ID：${activeDetailTarget.entityId}`,
            activeEntityOption?.designed || activeEntityOption?.hasDetailPlan
              ? '状态：已设计'
              : '状态：待设计'
          ]
        }
      : activeApiEndpoint
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
              activePageOption?.purpose ||
              activePage?.purpose ||
              application.senario ||
              '当前应用页面',
            keyFeatures: activePage?.keyFeatures || []
          }
  const activeHeaderStatus = pageContextStatus(
    activeDetailTarget.type === 'entity'
      ? Boolean(activeEntityOption?.designed || activeEntityOption?.hasDetailPlan)
      : activeApiEndpoint
        ? Boolean(
            activeApiEndpointOption?.endpoint.designed ||
              activeApiEndpointOption?.endpoint.hasDetailPlan
          )
        : Boolean(
            activePageOption?.designed || activePageOption?.hasDetailPlan || activePage?.design
          ),
    activeDetailTarget.type === 'entity'
      ? []
      : activeApiEndpoint
        ? []
        : activePage?.developmentTasks || [],
    displayedPlanExecutionMode,
    activeHeaderTarget.type,
    activeDetailTarget.type === 'entity'
      ? undefined
      : activeApiEndpoint
        ? undefined
        : activePageOption?.taskSummary
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
    (activeWorkflowPhase === 'entity_source_binding' ||
      workflowHasDetailReview(latestWorkflowForDisplay))
  const detailProgressVisible =
    loading &&
    activeWorkflowMatchesTarget &&
    (generatingDetailTargetKey === activeTargetKey ||
      activeWorkflowPhase === 'entity_source_binding') &&
    developmentPlanningReady &&
    Boolean(activeApiEndpoint || activePageOption) &&
    !detailConfirmationWaitingReview
  const initialDetailDesignSelectionRequired = requiresInitialDetailDesignSelection(hasPageDesigns)
  const hasActiveDetailWorkflow =
    interactingDetailTargetKey === activeTargetKey &&
    Boolean(
      activeApiEndpoint ||
        activePageOption ||
        activeEntityOption ||
        activeSession ||
        latestWorkflowForDisplay
    )
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
  const entityDetailTarget = activeDetailTarget.type === 'entity' ? activeDetailTarget : undefined
  // 实体已绑定专属会话时直接展示设计对话；已设计且无活动会话时展示信息面板（查看设计）；
  // 未设计实体与页面/接口保持一致，由锁定引导卡片接管，避免直接落入对话区。
  const entitySessionActive = Boolean(
    entityDetailTarget && activeSession?.entityId === entityDetailTarget.entityId
  )
  const endpointSessionActive = Boolean(
    activeApiEndpoint &&
      activeSession?.apiContractId === activeApiEndpoint.apiContractId &&
      activeSession?.endpointId === activeApiEndpoint.endpointId
  )
  const showEndpointDetailDesignEntry = shouldShowEndpointDetailDesignEntry(
    activeApiEndpointOption?.endpoint,
    endpointSessionActive,
    messages.length
  )
  const showEntityInfoPanel = Boolean(
    entityDetailTarget &&
      Boolean(activeEntityOption?.designed || activeEntityOption?.hasDetailPlan) &&
      !entitySessionActive &&
      !detailProgressVisible
  )

  // 实体会话真实开始运行（进入 loading）时记录，切换会话后复位。
  useEffect(() => {
    entityRunWasLiveRef.current = false
  }, [activeSessionId])

  useEffect(() => {
    if (!loading || activeDetailTarget.type !== 'entity' || !entitySessionActive) return
    entityRunWasLiveRef.current = true
  }, [activeDetailTarget, entitySessionActive, loading])

  // 实体设计确认后继续进入构建；只有整次运行以完成态真正结束时才
  // 回到实体信息展示界面。设计过程中每次动作（选数据源、AI 辅助、
  // 绑定提交等）都是一次子 run 并以 requires_user_input 结束，
  // 不能据此清空会话，否则会突然弹回引导卡片。
  useEffect(() => {
    if (activeDetailTarget.type !== 'entity') return
    // 只对当前会话内刚结束的实时运行触发跳转，避免打开历史设计会话时误跳。
    if (!activeWorkflow || loading) return
    const workflow = activeWorkflow
    const events = Array.isArray(workflow.events) ? workflow.events : []
    const runFinished = events.some((event) => String(event.type || '') === 'workflow.run.finished')
    if (!runFinished) return
    const runStatus = String(workflow.summary.status || '')
    if (runStatus !== 'completed' && runStatus !== 'finished') return
    // 历史会话恢复的已完成快照同样带 run.finished；只有本应用会话内
    // 真实运行过（loading 过）的 run 完成才允许跳回信息面板。
    if (!entityRunWasLiveRef.current) return
    const runId = workflow.runId
    if (!runId || handledEntityDesignRunRef.current.has(runId)) return
    handledEntityDesignRunRef.current.add(runId)
    entityRunWasLiveRef.current = false
    setEntityDesignReturning(true)
    clearActiveSession()
    onPlanningArtifactsRefresh()
  }, [activeDetailTarget, activeWorkflow, clearActiveSession, loading, onPlanningArtifactsRefresh])

  // 大纲刷新把实体标记为已设计后，解除返回态抑制，避免误锁后续入口。
  useEffect(() => {
    if (
      entityDesignReturning &&
      Boolean(activeEntityOption?.designed || activeEntityOption?.hasDetailPlan)
    ) {
      setEntityDesignReturning(false)
    }
  }, [activeEntityOption, entityDesignReturning])

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
      if (currentTarget.type === 'entity') return currentTarget
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
    setFreeChatSelected(
      !session.pageId && !session.apiContractId && !session.endpointId && !session.entityId
    )
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
    if (session?.entityId) {
      setActiveDetailTarget({
        type: 'entity',
        entityId: session.entityId,
        label: session.entityLabel || session.title
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
    onRightPanelOpenChange(true)
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
    onRightPanelOpenChange(true)
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
    const endpoint = developmentPlanningApiContracts
      .flatMap((contract) =>
        contract.endpoints.map((candidate) => ({
          apiContractId: candidate.apiContractId || contract.id,
          endpoint: candidate
        }))
      )
      .find(
        (candidate) =>
          candidate.apiContractId === target.apiContractId &&
          candidate.endpoint.id === target.endpointId
      )?.endpoint
    // 待设计接口与未绑定实体一致：点击大纲先回到绿色设计入口，历史会话仍可从子列表打开。
    if (requiresEndpointDetailDesign(endpoint)) {
      clearActiveSession()
      return
    }
    handleSelectEndpoint(target.apiContractId, target.endpointId).catch(() => undefined)
  }

  /** 从应用大纲切换实体；实体与页面/API 目标互斥，因此会清空当前页面和接口选中态。 */
  const handleEntitySelect = (entity: DevelopmentPlanningEntityOption): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setFreeChatSelected(false)
    setInteractingDetailTargetKey(`entity:${entity.id}`)
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'entity', entityId: entity.id, label: entity.label })
    handleSelectEntity(entity.id).catch(() => undefined)
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

  /** 启动当前接口开发；后端先执行实体绑定前置检查。 */
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
    const started = await handleStartEndpointDevelopment({
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

  /** 从目标选择器启动页面/API开发或独立实体数据源绑定。 */
  const handleInitialDetailTargetSelect = async (
    targetType: 'page' | 'endpoint' | 'entity',
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
    targetType: 'page' | 'endpoint' | 'entity',
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
    if (targetType === 'entity') {
      setFreeChatSelected(false)
      const targetKey = `entity:${targetId}`
      setInteractingDetailTargetKey(targetKey)
      setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
      setActiveDetailTarget({ type: 'entity', entityId: targetId, label: targetLabel })
      const started = await handleStartEntityDetailConfirmation({
        entityId: targetId,
        entityLabel: targetLabel,
        hasDetailPlan
      })
      if (started) {
        onPlanningArtifactsRefresh()
      } else {
        setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
      }
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

  /** 从实体设计门禁卡片一键跳转到对应实体的设计流程。 */
  const handleEntityDesignGateJump = async (entityId: string): Promise<void> => {
    const entity = developmentPlanningEntities.find((item) => item.id === entityId)
    await handleStartDetailDesign(
      'entity',
      entityId,
      entity?.label || entityId,
      Boolean(entity?.hasDetailPlan)
    )
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    const session = sessions.find((item) => item.id === sessionId)
    setFreeChatSelected(
      Boolean(
        session &&
          !session.pageId &&
          !session.apiContractId &&
          !session.endpointId &&
          !session.entityId
      )
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
    } else if (session?.entityId) {
      setActiveDetailTarget({
        type: 'entity',
        entityId: session.entityId,
        label: session.entityLabel || session.title
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
    // 设计阶段：规划确认走 planningSubmitRef（Modal 的 runPlanning），不走开发 workflow。
    if (isDesignPhase) {
      // 空答案 = UI 设计稿生成池轮询（no-op resume）：不开启新一轮、不追加用户消息，
      // 也不走 ensureApplicationPlanningAction（空 answers 会被误判为 confirm）。
      // 直接把空 answers 传给 onSubmitPlanningClarification，由 Modal 拦截走恢复路径。
      const isUiDesignPoll = !answers || Object.keys(answers).length === 0
      if (isUiDesignPoll) {
        onSubmitPlanningClarification(workflow, {}, editedRequirementSpec)
        return
      }
      const planningAnswers = ensureApplicationPlanningAction(workflow, answers)
      // UI 设计稿的单页动作（换一换/选模板/调整）是同一轮内的更新，不新增消息卡片，
      // 只更新现有卡片；跳过与确认全部等推进到下一阶段的操作才新增卡片并留痕。
      const isUiDesignPageAction =
        planningAnswers && 'ui_design_action' in planningAnswers
          ? (planningAnswers as { ui_design_action?: { action?: string } }).ui_design_action
              ?.action !== 'skip'
          : false
      if (!isUiDesignPageAction) {
        planningNewRoundRef.current = true
        // 用户操作留痕：把确认/放弃/填表等操作作为 user 消息追加到对话区。
        appendPlanningUserMessage(planningAnswers)
      }
      onSubmitPlanningClarification(workflow, planningAnswers, editedRequirementSpec)
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
        setDesignDocFileContent((current) => ({
          ...current,
          'requirement-spec': saved.artifact.content
        }))
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

  /** 把自由输入交给原创建规划 Graph 先做意图识别，当前等待阶段不能决定变更目标。 */
  const handleDesignChangeSend = async (): Promise<void> => {
    const trimmed = draft.trim()
    if (!trimmed || !planningWorkflow) return
    planningNewRoundRef.current = true
    appendPlanningUserMessage({ design_change_request: trimmed })
    onSubmitPlanningClarification(planningWorkflow, {}, undefined, undefined, trimmed)
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
    markApplicationEnteredDevelopment(application.id)
    setEnterDevConfirmed(true)
    clearActiveSession()
    setActiveDetailTarget({ type: 'none' })
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setFreeChatSelected(false)
    setRightPanel(undefined)
    setActiveView('chat')
    switchPhase(null)
  }, [application.id, switchPhase, clearActiveSession])

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
          onEntitySelect={handleEntitySelect}
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
          entities={developmentPlanningEntities}
          selectedApiEndpointKey={activeApiEndpoint?.endpointKey || ''}
          selectedEntityId={activeDetailTarget.type === 'entity' ? activeDetailTarget.entityId : ''}
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
        ) : showEntityInfoPanel ? (
          <div className={cx('ai-chat-main')}>
            <EntityInfoPanel
              entity={activeEntityOption}
              theme={theme}
              workspaceRoot={application.workspaceRoot || ''}
            />
          </div>
        ) : detailTargetSelectionRequired ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              apiContracts={developmentPlanningApiContracts}
              disabled={loading || workspaceBusy}
              entities={developmentPlanningEntities}
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
                previewLaunchError={showPreviewActions ? runtimePreviewLaunchError : ''}
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
              entityDesignSession={entityDesignChatActive}
              designPhasePlanning={isDesignPhase}
              error={planningError || error}
              key={activeSession?.key || draftKey}
              loading={loading}
              messages={messages}
              onEntityDesignGateJump={handleEntityDesignGateJump}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onRevertCodeChanges={requestCodeChangeRevert}
              onRetryError={planningError ? onRetryPlanning : undefined}
              onSubmitClarification={handleSubmitWorkflowClarification}
              revertingCodeChangeIds={revertingCodeChangeIds}
              workspaceRoot={application.workspaceRoot || undefined}
              uiDesignActivePageId={uiDesignActivePageId}
              onUiDesignActivePageChange={handleUiDesignActivePageChange}
              uiDesignActingPageIds={uiDesignActingPageIds}
              onUiDesignActingPageIdsChange={setUiDesignActingPageIds}
              onSaveRequirementSpec={handleSaveRequirementSpec}
              rootPath={application.schema?.menus?.rootPath || '/'}
              onEnterDevelopment={handleEnterDevelopment}
              generatingTemplate={generatingTemplate}
              planningWorkflow={planningWorkflow}
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

            {!entityDesignChatActive &&
            shouldRenderPlanExecutionDock(displayedPlanExecutionMode, conversationActive) ? (
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
            ) : designChangeInputLocked ? (
              <DesignChangeLockDock
                disabled={loading || workspaceBusy}
                onStart={async () => {
                  await onStopPlanning()
                  setDesignChangeUnlocked(true)
                }}
              />
            ) : (
              <>
                <ChatComposer
                  activeWorkflow={activeWorkflow}
                  copy={copy}
                  draft={draft}
                  inputMode={effectiveInputMode}
                  inputModeDisabled={inputModeLocked}
                  loading={loading}
                  onDraftChange={(value) => setDraftByKey(draftKey, value)}
                  onInputModeChange={
                    activeDetailTarget.type === 'none' || entityDesignChatActive
                      ? undefined
                      : handleInputModeChange
                  }
                  onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                  // 创建规划仍可修订时，自由输入先做设计意图识别；模板就绪后则恢复普通自由对话。
                  // 当前节点的澄清和确认只能通过上方结构化卡片提交，不能劫持自由输入语义。
                  onSend={designChangeWorkflowAvailable ? handleDesignChangeSend : handleSend}
                  onStopGenerating={handleStopGenerating}
                  stopping={stopping}
                  selectedSkills={selectedSkills}
                  workspaceBusy={workspaceBusy}
                  workspaceRoot={workspaceRoot}
                />
                {!entityDesignChatActive &&
                displayedPlanExecutionMode !== 'idle' &&
                !conversationActive ? (
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

            {/* 未完成的实体绑定与 endpoint 设计都先显示统一绿色入口卡片。 */}
            {((requiresEntitySourceBinding(activeEntityOption) &&
              entityDesignChatActive &&
              !entitySessionActive &&
              !entityDesignReturning) ||
              showEndpointDetailDesignEntry) &&
            displayedPlanExecutionMode === 'idle' &&
            !detailConfirmationWaitingReview ? (
              <DetailConfirmationPageSelector
                disabled={loading || workspaceBusy}
                entities={developmentPlanningEntities}
                generating={false}
                loading={false}
                mode="locked"
                onStart={handleStartDetailDesign}
                pages={displayedPlanningPages}
                selectedEntity={
                  activeDetailTarget.type === 'entity'
                    ? {
                        entityId: activeDetailTarget.entityId,
                        label: activeDetailTarget.label,
                        hasDetailPlan: Boolean(activeEntityOption?.hasDetailPlan),
                        purpose: activeEntityOption?.purpose
                      }
                    : undefined
                }
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
                actingPageIds={uiDesignActingPageIds}
                onPageChange={setUiDesignActivePageId}
                pages={uiDesignPages}
              />
            ) : isDesignPhase ? (
              <DocPanel
                content={designDocContent}
                docName={designDocName}
                generating={designDocGenerating || designDocLoading}
                productPlan={requirementDocViewActive ? requirementProductPlanForDoc : productPlanForDoc}
                requirementSpec={requirementSpecForDoc}
                technicalPlan={technicalPlanForDoc}
                structuredDocument={
                  technicalPlanViewActive
                    ? 'technical-plan'
                    : requirementDocViewActive
                      ? 'requirement-doc'
                      : undefined
                }
                structuredDocumentLoading={
                  technicalPlanViewActive ? technicalPlanViewLoading : false
                }
                title={designDocTitle}
              />
            ) : (
              <DocPanel
                content={designDocContent}
                docName={designDocName}
                generating={designDocGenerating || designDocLoading}
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

import { HolderOutlined } from '@ant-design/icons'
import { Alert, message } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench, useWorkbenchPhase } from '../../context'
import {
  hasApplicationEnteredDevelopment,
  isApplicationTemplatePreparationEligible,
  markApplicationEnteredDevelopment,
  subscribeApplicationDevelopmentEntry,
  WORKBENCH_PHASE_AGENTS
} from '../../workbenchPhase'
import type { WorkbenchPhase } from '../../workbenchPhase'
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
  InspectedElementContext,
  WorkflowDebugOptions,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowClarificationAnswers,
  WorkflowDevelopmentContinuation,
  WorkflowDesignStageRevisionStart,
  WorkflowWorkbenchPlanRevisionStart,
  WorkflowRevisionContinuation,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import { CLASS_PREFIX, composePreviewUrl, cx, openPreviewWindow, previewOrigin } from '../../utils'
import { readWorkspaceFile } from '../../service/workspaceTools'
import type { ChatSessionDevelopmentContinuation } from '../../service/chatSessions'
import { saveRequirementSpecDraft } from '../../service/applicationPagePlanning'
import type { WorkflowRevisionContinuationHandoff } from '../../service/applicationPagePlanning'
import { isAuthenticationFailure } from '../../service/authentication'
import { formatError } from '../Welcome/utils'
import {
  planningWorkflowActivity,
  planningWorkflowClarification,
  planningWorkflowPhase,
  planningRequirementsConfirmed,
  ensureApplicationPlanningAction,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  planningWorkflowUiDesignSkipped,
  retainApplicationPlanningInterrupt,
  shouldBackfillPlanningWorkflow,
  shouldSuppressConfirmedTechnicalPlanTransitionChunk
} from '../Welcome/planningWorkflowState'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import AcceptanceDecisionDock from './components/AcceptanceDecisionDock'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import DesignChangeLockDock from './components/DesignChangeLockDock'
import SessionExecutionLockDock from './components/SessionExecutionLockDock'
import DocPanel from './components/DocPanel'
import SourcePanel from './components/SourcePanel'
import StageOutputPanel from './components/StageOutputPanel'
import DevelopmentArtifactsPanel from './components/DevelopmentArtifactsPanel'
import UiDesignPreviewPanel from './components/UiDesignPreviewPanel'
import MessageList from './components/MessageList'
import {
  appendPlanningLoadingPlaceholder,
  compactPlanningMessageHistory
} from './components/MessageList/uiDesignPreviewHistory'
import PageContextHeader from './components/PageContextHeader'
import type { PageContextStatus } from './components/PageContextHeader'
import PlanExecutionDock from './components/PlanExecutionDock'
import RightPanelTabs, {
  type WorkspaceTab,
  type WorkspaceTabKey
} from './components/RightPanelTabs'
import SessionSidebar from './components/SessionSidebar'
import TemporaryChatOverlay from './components/TemporaryChatOverlay'
import QuickTaskGuide from './components/QuickTaskGuide'
import type { QuickTaskItem } from './components/QuickTaskGuide/quickTasks'
import WorkspaceDebugDock from './components/WorkspaceDebugDock'
import EntityInfoPanel from './components/EntityInfoPanel'
import type { ClarificationAnswers } from './components/WorkflowRunCard'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import DataSourcesPage from '../DataSourcesPage/DataSourcesPage'
import DetailConfirmationPageSelector from '../DetailConfirmationPageSelector'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useDevelopmentArtifactDetail } from './hooks/useDevelopmentArtifactDetail'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useCodeReviewReportPanel } from './hooks/useCodeReviewReportPanel'
import { useTestReportPanel } from './hooks/useTestReportPanel'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { useSessionRuntimeStore } from './hooks/useSessionRuntimeStore'
import {
  activeFormalRevisionStageSession,
  bindRevisionSessionChangeId,
  createFormalRevisionSessionContext,
  formalRevisionContinuationSourceSession,
  formalRevisionPlanningSourceSession,
  initialFormalRevisionPhase,
  planningStageTransitionKey,
  recoverableRevisionDevelopmentExecution,
  revisionDevelopmentSessionForContinuation
} from './hooks/revisionSession'
import { sessionIdentityFromSummary, sessionRuntimeKey } from './hooks/sessionRuntime'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { chatCopy } from './constants'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import {
  workflowDevelopmentContinuation
} from './developmentContinuation'
import {
  currentDagConfirmationErrors,
  currentDagConfirmationPlan,
  currentDagConfirmationTargetReview,
  latestDagGenerationSnapshot,
  pendingDagConfirmationExecution,
  pendingDagConfirmationWorkflow,
  runningDagGenerationStage,
  selectedDagGenerationStage,
  stageOutputPhase
} from './stageOutputState'
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  requiresEntitySourceBinding,
  shouldShowEndpointDetailDesignEntry,
  shouldShowPageDetailDesignEntry,
  workflowShouldShowCodeChanges,
  workflowDetailTargetKey,
  type WorkflowPreviewTarget
} from './utils'
import { isConversationWorkflow } from './conversationMode'
import {
  planningArtifactRecoveryKeys,
  type PlanningArtifactRecoveryKey
} from './planningArtifactRecovery'
import {
  deriveDisplayedPlanExecutionMode,
  planExecutionShowsDebugResume,
  planExecutionContextForEndpoint,
  planExecutionContextForPage,
  planExecutionContextForRun,
  shouldRenderPlanExecutionDock,
  workflowCanRetryFailedTasks,
  workflowCodeReviewRetry,
  workflowInteractionAvailability,
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
  // “跳过”只结束 UI 设计，不会越过独立规划阶段入口。
  if ('ui_design_action' in answers) {
    const action = (answers as { ui_design_action?: { action?: string } }).ui_design_action
    return action?.action === 'skip' ? '跳过 UI 设计稿，等待进入规划阶段' : ''
  }
  // 入口卡片本身就是用户操作记录，不再追加同文案的 user 消息。
  if (answers.__applicationPlanningAction === 'enter_planning') return ''
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
  planning_stage_entry: '规划阶段入口',
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

// 创建规划确认卡的稳定 mode 集合，用于把同一门禁的多次流式快照合并到原卡片。
const PLANNING_CONFIRMATION_MODES = new Set([
  'requirement_document_confirmation',
  'technical_plan_confirmation',
  'ui_design_confirmation',
  'planning_stage_entry_confirmation'
])

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
  ) => Promise<void>
  onStopPlanning: () => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: 'light' | 'dark') => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  onSessionHistoryReadyChange: (ready: boolean) => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 设计阶段规划 Graph 的错误，来自仍在后台挂载的规划容器。 */
  planningError?: string
  /** 从工作台错误卡片重试规划 Graph。 */
  onRetryPlanning?: () => void
  planningThreadId?: string
  planningWorkflow?: WorkflowRunPayload
  /** 仅冷恢复时允许从 .xcodeagent 读取当前阶段规划产物。 */
  restorePlanningArtifactsFromDisk?: boolean
  theme: 'light' | 'dark'
  rightPanelOpen: boolean
  onRightPanelOpenChange: (open: boolean) => void
}

type ActiveView = 'chat' | 'skills' | 'files' | 'settings' | 'dataSources'

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

type DesignDocArtifactKey = PlanningArtifactRecoveryKey

type DesignWorkspaceDoc = {
  key: WorkspaceDocKey
  title: string
  path: string
  content: string
  available: boolean
}

type LocalDesignMarkdown = {
  content: string
  path: string
}

type LocalDesignArtifactSnapshot = {
  document?: LocalDesignMarkdown
  key: DesignDocArtifactKey
  structured?: Record<string, unknown>
}

type LocalDesignWorkspaceSnapshot = {
  artifacts: LocalDesignArtifactSnapshot[]
}

// 同一 renderer 内每个应用只执行一次冷恢复；缓存 Promise 同时合并 React StrictMode 重放。
const localDesignRecoveryCache = new Map<string, Promise<LocalDesignWorkspaceSnapshot>>()

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

/** 读取单个规划产物及其结构化快照，仅供应用冷恢复使用。 */
async function readLocalDesignArtifactSnapshot(
  workspaceRoot: string,
  artifact: (typeof LOCAL_DESIGN_ARTIFACTS)[number]
): Promise<LocalDesignArtifactSnapshot> {
  const documentPromise = readLocalDesignMarkdown(workspaceRoot, artifact)
    .then((document) => (document.content.trim() ? document : undefined))
    .catch(() => undefined)
  const structuredPromise =
    artifact.key === 'requirement-spec'
      ? readLocalRequirementSpecJson(workspaceRoot)
      : artifact.key === 'product-plan'
        ? readLocalProductPlanJson(workspaceRoot)
        : artifact.key === 'technical-plan'
          ? readLocalTechnicalPlanJson(workspaceRoot)
          : Promise.resolve(undefined)
  const [document, storedStructured] = await Promise.all([documentPromise, structuredPromise])
  let structured = storedStructured
  if (artifact.key === 'ui-design' && document) {
    try {
      structured = asWorkflowRecord(JSON.parse(document.content))
    } catch {
      structured = undefined
    }
  }
  return { document, key: artifact.key, structured }
}

/** 冷恢复时只并行读取当前阶段声明的本地产物。 */
async function readLocalDesignWorkspaceSnapshot(
  workspaceRoot: string,
  keys: readonly PlanningArtifactRecoveryKey[]
): Promise<LocalDesignWorkspaceSnapshot> {
  const artifacts = LOCAL_DESIGN_ARTIFACTS.filter((artifact) => keys.includes(artifact.key))
  return {
    artifacts: await Promise.all(
      artifacts.map((artifact) => readLocalDesignArtifactSnapshot(workspaceRoot, artifact))
    )
  }
}

/** 优先读取当前 AG-UI 内存快照，本地 Markdown 只作为冷恢复兜底。 */
function designDocContentFor(
  localContents: Record<string, string>,
  workflow: WorkflowRunPayload | undefined,
  key: DesignDocArtifactKey
): string {
  const confirmationArtifact = workflow?.confirmationArtifact
  const expectedArtifactId =
    key === 'requirement-spec'
      ? 'requirement_spec'
      : key === 'product-plan'
        ? 'product_plan'
        : key === 'technical-plan'
          ? 'technical_plan'
          : undefined
  if (
    confirmationArtifact &&
    confirmationArtifact.id === expectedArtifactId &&
    confirmationArtifact.content.trim()
  ) {
    return confirmationArtifact.content
  }
  return localContents[key] || ''
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
  // 当前确认产物是实时内存权威；ProductPlan 已包含合并文档，RequirementSpec 则直接展示本轮草稿。
  const artifact = workflow?.confirmationArtifact
  if (
    (artifact?.id === 'product_plan' || artifact?.id === 'requirement_spec') &&
    artifact.content.trim()
  ) {
    return artifact.content
  }
  const requirementDoc = (localContents['requirement-spec'] || '').trimEnd()
  const productPlanDoc = (localContents['product-plan'] || '').trim()
  if (productPlanDoc) {
    const mergedPlan = demoteLeadingH1(productPlanDoc)
    return requirementDoc.trim() ? `${requirementDoc}\n\n${mergedPlan}` : mergedPlan
  }
  if (requirementDoc.trim()) return requirementDoc
  return ''
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
    mode === 'awaiting_acceptance_phase_confirmation' ||
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

/** 组织应用侧栏、对话区、页面信息与预览面板的主工作台。 */
export default function AiChatPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
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
  onStartDesignStageRevision,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  onSessionHistoryReadyChange,
  generatingTemplate,
  planningError,
  onRetryPlanning,
  planningThreadId,
  planningWorkflow,
  restorePlanningArtifactsFromDisk,
  theme,
  rightPanelOpen,
  onRightPanelOpenChange
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  // 临时对话仅控制覆盖层可见性，不切换当前工作流会话或持久化上下文。
  const [temporaryChatOpen, setTemporaryChatOpen] = useState(false)
  // 设计阶段自由变更是主规划 Workflow 的显式中断模式，默认保持锁定。
  const [interactingDetailTargetKey, setInteractingDetailTargetKey] = useState('')
  const [generatingDetailTargetKey, setGeneratingDetailTargetKey] = useState('')
  // 验收阶段拒绝结果仅恢复普通对话，不改变后端 page_acceptance 待验收状态。
  const [acceptanceConversationSessionKey, setAcceptanceConversationSessionKey] = useState('')
  // 元素审查：是否激活 + 当前审查的元素上下文。两者语义相关，合并减少 state 数量。
  const [inspection, setInspection] = useState<{
    active: boolean
    context: InspectedElementContext | undefined
  }>({ active: false, context: undefined })
  // 兼容别名：保持下游调用点不变。
  const elementInspectionActive = inspection.active
  const inspectedElementContext = inspection.context
  const setElementInspectionActive = useCallback(
    (active: boolean) => setInspection((s) => ({ ...s, active })),
    []
  )
  const setInspectedElementContext = useCallback(
    (context: InspectedElementContext | undefined | ((prev: InspectedElementContext | undefined) => InspectedElementContext | undefined)) =>
      setInspection((s) => ({
        ...s,
        context: typeof context === 'function' ? context(s.context) : context
      })),
    []
  )
  // UI 设计稿预览：当前选中页面 id + 正在执行动作的 pageId 集合。两者语义相关，合并减少 state 数量。
  const [uiDesignPreview, setUiDesignPreview] = useState<{
    activePageId: string
    actingPageIds: string[]
  }>({ activePageId: '', actingPageIds: [] })
  // 兼容别名：保持下游调用点不变。
  const uiDesignActivePageId = uiDesignPreview.activePageId
  const uiDesignActingPageIds = uiDesignPreview.actingPageIds
  const setUiDesignActivePageId = useCallback(
    (activePageId: string) => setUiDesignPreview((s) => ({ ...s, activePageId })),
    []
  )
  const setUiDesignActingPageIds = useCallback(
    (actingPageIds: string[]) => setUiDesignPreview((s) => ({ ...s, actingPageIds })),
    []
  )
  // 设计阶段右侧文档：文件内容/路径缓存 + 各产物 JSON + 加载态。
  // 这 7 个 state 在磁盘恢复时一起 set（行 ~1161-1176），合并减少渲染批次。
  // 注意：不能叫 designDocs，因为行 ~1092 已有派生变量 designDocs。
  const [designDocState, setDesignDocState] = useState<{
    fileContent: Record<string, string>
    filePath: Record<string, string>
    technicalPlan: Record<string, unknown> | undefined
    productPlan: Record<string, unknown> | undefined
    requirementSpec: Record<string, unknown> | undefined
    uiDesign: Record<string, unknown> | undefined
    loading: boolean
  }>({
    fileContent: {},
    filePath: {},
    technicalPlan: undefined,
    productPlan: undefined,
    requirementSpec: undefined,
    uiDesign: undefined,
    loading: false
  })
  // 兼容别名：保持下游调用点不变。
  const designDocFileContent = designDocState.fileContent
  const designDocFilePath = designDocState.filePath
  const technicalPlanFile = designDocState.technicalPlan
  const productPlanFile = designDocState.productPlan
  const requirementSpecFile = designDocState.requirementSpec
  const uiDesignFile = designDocState.uiDesign
  const technicalPlanFileLoading = designDocState.loading
  const setDesignDocFileContent = useCallback(
    (fileContent: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) =>
      setDesignDocState((s) => ({
        ...s,
        fileContent: typeof fileContent === 'function' ? fileContent(s.fileContent) : fileContent
      })),
    []
  )
  const setDesignDocFilePath = useCallback(
    (filePath: Record<string, string>) => setDesignDocState((s) => ({ ...s, filePath })),
    []
  )
  const setTechnicalPlanFile = useCallback(
    (technicalPlan: Record<string, unknown> | undefined) => setDesignDocState((s) => ({ ...s, technicalPlan })),
    []
  )
  const setProductPlanFile = useCallback(
    (productPlan: Record<string, unknown> | undefined) => setDesignDocState((s) => ({ ...s, productPlan })),
    []
  )
  const setRequirementSpecFile = useCallback(
    (requirementSpec: Record<string, unknown> | undefined) => setDesignDocState((s) => ({ ...s, requirementSpec })),
    []
  )
  const setUiDesignFile = useCallback(
    (uiDesign: Record<string, unknown> | undefined) => setDesignDocState((s) => ({ ...s, uiDesign })),
    []
  )
  const setTechnicalPlanFileLoading = useCallback(
    (loading: boolean) => setDesignDocState((s) => ({ ...s, loading })),
    []
  )
  // 当前工作台进入规划阶段时创建独立聊天线程；后端 Graph 继续复用原 planningThreadId。
  const [localPlanningConversationThreadId, setLocalPlanningConversationThreadId] = useState('')
  // 同一 impact 只允许创建一个前端二次修改会话，失败后才放开重试。
  const designRevisionStartInteractionRef = useRef('')
  const formalRevisionSessionIdentitiesRef = useRef<Record<string, SessionIdentity>>({})
  const formalRevisionSourcePhasesRef = useRef<Record<string, WorkbenchPhase>>({})
  // 二次修改：待处理的 continuation + 设计变更解锁标记。两者语义相关，合并减少 state 数量。
  const [revisionState, setRevisionState] = useState<{
    pendingContinuation: {
      continuation: WorkflowRevisionContinuation
      reject: (reason?: unknown) => void
      resolve: () => void
      sourceIdentity: SessionIdentity
      targetIdentity: SessionIdentity
    } | undefined
    designChangeUnlocked: boolean
  }>({ pendingContinuation: undefined, designChangeUnlocked: false })
  // 兼容别名：保持下游调用点不变。
  const pendingRevisionContinuation = revisionState.pendingContinuation
  const designChangeUnlocked = revisionState.designChangeUnlocked
  const setPendingRevisionContinuation = useCallback(
    (pendingContinuation: typeof revisionState.pendingContinuation) =>
      setRevisionState((s) => ({ ...s, pendingContinuation })),
    []
  )
  const setDesignChangeUnlocked = useCallback(
    (designChangeUnlocked: boolean) =>
      setRevisionState((s) => ({ ...s, designChangeUnlocked })),
    []
  )
  // 同一 change 的 handoff 在当前进程只执行一次；失败后删除，允许用户显式重试。
  const revisionDevelopmentHandoffPromisesRef = useRef<Record<string, Promise<void>>>({})
  // 同一 lifecycle execution 只恢复一次缺失的 DEVELOPMENT 会话和规划回执。
  const revisionDevelopmentRecoveryRef = useRef('')
  // 预览：错误信息 + 运行时 baseUrl + 启动错误。三者强联动（一起 set），合并减少 state 数量。
  const [preview, setPreview] = useState<{
    error: string
    baseUrl: string
    launchError: string | undefined
  }>(() => ({
    error: '',
    baseUrl: previewOrigin(previewBaseUrl),
    launchError: previewLaunchError
  }))
  // 兼容别名：保持下游调用点不变。
  const previewError = preview.error
  const runtimePreviewBaseUrl = preview.baseUrl
  const runtimePreviewLaunchError = preview.launchError
  const setPreviewError = useCallback(
    (error: string) => setPreview((s) => ({ ...s, error })),
    []
  )
  const setRuntimePreviewBaseUrl = useCallback(
    (baseUrl: string) => setPreview((s) => ({ ...s, baseUrl })),
    []
  )
  const setRuntimePreviewLaunchError = useCallback(
    (launchError: string | undefined) => setPreview((s) => ({ ...s, launchError })),
    []
  )
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
  const {
    phase: activeWorkbenchPhase,
    derivedPhase: derivedWorkbenchPhase,
    switchPhase
  } = useWorkbenchPhase()
  const isDesignPhase = activeWorkbenchPhase === 'product'
  const isTechnicalPlanningPhase = activeWorkbenchPhase === 'planning'
  const isApplicationPlanningPhase = isDesignPhase || isTechnicalPlanningPhase
  const {
    acquireSessionExecution,
    releaseSessionExecution,
    sessionExecutions,
    updateSessionExecutionStatus
  } = useSessionRuntimeStore()

  // 切换应用或规划线程时回到主流程锁定态，避免把上一个规划的自由变更模式带入新会话。
  useEffect(() => {
    setDesignChangeUnlocked(false)
  }, [application.id, isApplicationPlanningPhase, planningThreadId])
  useEffect(() => {
    setAcceptanceConversationSessionKey('')
  }, [application.id, planningThreadId])
  // 二次修改会话去重只在当前应用内有效；切换应用时必须清空，避免相同 interactionId 串用旧身份。
  useEffect(() => {
    designRevisionStartInteractionRef.current = ''
    formalRevisionSessionIdentitiesRef.current = {}
  }, [application.id, application.workspaceRoot])
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
  // “进入开发”可能来自模板卡或顶部阶段切换；任一路径确认后都永久关闭本应用的模板卡。
  useEffect(() => {
    setEnterDevConfirmed(hasApplicationEnteredDevelopment(application.id))
    return subscribeApplicationDevelopmentEntry(application.id, () => {
      setEnterDevConfirmed(true)
    })
  }, [application.id])
  const applicationTemplatePreparationEligible = isApplicationTemplatePreparationEligible(
    application.source,
    enterDevConfirmed
  )
  // 只在 lifecycle 首次到达 ready_for_workbench 时锁一次，不依赖 activeWorkbenchPhase（避免覆盖用户切换）。
  const planningConfirmedSeenRef = useRef(false)
  useEffect(() => {
    if (!lifecycleReadyForWorkbench) return
    if (planningConfirmedSeenRef.current) return
    planningConfirmedSeenRef.current = true
    if (enterDevConfirmed) return
    // 后端已完成模板生成（lifecycle=ready_for_workbench）：锁住规划阶段，等用户手动进入开发。
    switchPhase('planning')
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
  const { artifactDetailLabel, artifactOutlineProps } = useDevelopmentArtifactDetail({
    applicationId: application.id,
    setRightPanel,
    onRightPanelOpenChange
  })
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

  // ---- 创建规划阶段：设计阶段展示产品产物，规划阶段只展示 TechnicalPlan。 ----
  // 需求文档在模型生成后即可展示；确认状态只决定它是草稿还是正式文档。
  // UI 设计稿：从规划 workflow 的 clarification（ui_design_confirmation 模式）或
  // state/result 的 ui_designs 读取页面列表。设计稿生成中或已就绪都算可用。
  const planningClarification = planningWorkflow
    ? planningWorkflowClarification(planningWorkflow)
    : undefined
  const planningPhaseRunning = planningWorkflow?.summary?.status === 'running'
  const planningPhase = planningWorkflowPhase(planningWorkflow)
  const planningUiDesignSkipped = planningWorkflowUiDesignSkipped(planningWorkflow)
  const requirementSpecPath =
    workflowArtifactPath(planningWorkflow, 'requirement-spec') ||
    designDocFilePath['requirement-spec']
  const requirementsConfirmed = planningRequirementsConfirmed(planningWorkflow, requirementSpecPath)
  const localUiDesigns = asWorkflowRecord(uiDesignFile?.ui_designs)
  const localUiDesignPages: unknown[] | undefined = Array.isArray(uiDesignFile?.pages)
    ? uiDesignFile.pages
    : Array.isArray(localUiDesigns?.pages)
      ? localUiDesigns.pages
      : undefined
  const planningUiDesignPagesSource: unknown[] | undefined = planningUiDesignSkipped
    ? undefined
    : Array.isArray(planningClarification?.pages) && planningClarification.pages.length > 0
      ? planningClarification.pages
      : ((planningWorkflow?.state?.ui_designs as { pages?: unknown[] } | undefined)?.pages ??
        (planningWorkflow?.result?.ui_designs as { pages?: unknown[] | undefined } | undefined)
          ?.pages ??
        localUiDesignPages)
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
  const requirementSpecMemory = requirementSpecFromWorkflow(planningWorkflow)
  const productPlanMemory = productPlanFromWorkflow(planningWorkflow)
  const technicalPlanMemory = technicalPlanFromWorkflow(planningWorkflow)
  const requirementDocAvailable = Boolean(
    requirementDocContent.trim() || requirementSpecMemory || productPlanMemory
  )
  const uiDesignAvailable = Boolean(uiDesignDocContent.trim()) || uiDesignPages.length > 0
  const technicalPlanDocAvailable = Boolean(technicalPlanDocContent.trim() || technicalPlanMemory)
  // 按当前设计/规划阶段稳定生成右侧文档集合，避免阶段切换之外反复创建依赖对象。
  const designDocs = useMemo<DesignWorkspaceDoc[] | undefined>(() => {
    if (!isApplicationPlanningPhase) return undefined
    if (isDesignPhase) {
      const documents: DesignWorkspaceDoc[] = [
        {
          key: 'requirement-spec' as WorkspaceDocKey,
          title: requirementsConfirmed ? '需求文档' : '需求文档（草稿）',
          path: 'specs/requirement-spec.md',
          content: requirementDocContent,
          available: requirementDocAvailable
        }
      ]
      if (!planningUiDesignSkipped) {
        documents.push({
          key: 'ui-design' as WorkspaceDocKey,
          title: 'UI设计稿',
          path: 'specs/ui-designs',
          content: uiDesignDocContent,
          available: uiDesignAvailable
        })
      }
      return documents
    }
    return [
      {
        key: 'technical-plan' as WorkspaceDocKey,
        title: '技术规划',
        path: 'plans/technical-plan.json',
        content: technicalPlanDocContent,
        available: technicalPlanDocAvailable
      }
    ]
  }, [
    isApplicationPlanningPhase,
    isDesignPhase,
    requirementDocAvailable,
    requirementDocContent,
    requirementsConfirmed,
    planningUiDesignSkipped,
    technicalPlanDocAvailable,
    technicalPlanDocContent,
    uiDesignAvailable,
    uiDesignDocContent
  ])
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' && designDocs?.some((doc) => doc.key === rightPanel.docKey)
      ? rightPanel.docKey
      : undefined
  const activeDesignDoc = designDocs?.find((doc) => doc.key === activeDesignDocKey)
  const technicalPlanViewActive =
    isTechnicalPlanningPhase && activeDesignDocKey === 'technical-plan'
  const requirementDocViewActive = isDesignPhase && activeDesignDocKey === 'requirement-spec'
  // 右侧技术规划始终走结构化视图；运行中优先使用 Workflow，重开工作区时读取正式 JSON。
  const technicalPlanForDoc = technicalPlanViewActive
    ? technicalPlanMemory || technicalPlanFile
    : undefined
  const productPlanForDoc = technicalPlanViewActive
    ? productPlanMemory || productPlanFile
    : undefined
  const technicalPlanViewLoading =
    technicalPlanViewActive &&
    (!technicalPlanForDoc || !productPlanForDoc) &&
    technicalPlanFileLoading
  // 需求文档 tab 优先使用结构化可视化；结构化数据不可读时回退 Markdown。
  const requirementSpecForDoc = requirementDocViewActive
    ? requirementSpecMemory || requirementSpecFile
    : undefined
  const requirementProductPlanForDoc = requirementDocViewActive
    ? productPlanMemory || productPlanFile
    : undefined

  // 只有明确的冷恢复入口才读本地产物；同一 renderer 内缓存 Promise，阶段、事件和 tab 均不会重读。
  useEffect(() => {
    const workspaceRoot = application.workspaceRoot
    const recoveryKeys = planningArtifactRecoveryKeys(
      restorePlanningArtifactsFromDisk === true,
      activeWorkbenchPhase
    )
    if (!workspaceRoot || recoveryKeys.length === 0) {
      setTechnicalPlanFileLoading(false)
      return
    }

    let cancelled = false
    const recoveryKey = `${application.id}:${workspaceRoot}`
    let recovery = localDesignRecoveryCache.get(recoveryKey)
    if (!recovery) {
      recovery = readLocalDesignWorkspaceSnapshot(workspaceRoot, recoveryKeys)
      localDesignRecoveryCache.set(recoveryKey, recovery)
    }
    setTechnicalPlanFileLoading(recoveryKeys.includes('technical-plan'))

    void recovery
      .then(({ artifacts }) => {
        if (cancelled) return
        const documents = artifacts.filter(
          (artifact): artifact is LocalDesignArtifactSnapshot & { document: LocalDesignMarkdown } =>
            Boolean(artifact.document)
        )
        setDesignDocFileContent(
          Object.fromEntries(documents.map((artifact) => [artifact.key, artifact.document.content]))
        )
        setDesignDocFilePath(
          Object.fromEntries(documents.map((artifact) => [artifact.key, artifact.document.path]))
        )
        setTechnicalPlanFile(
          artifacts.find((artifact) => artifact.key === 'technical-plan')?.structured
        )
        setProductPlanFile(
          artifacts.find((artifact) => artifact.key === 'product-plan')?.structured
        )
        setRequirementSpecFile(
          artifacts.find((artifact) => artifact.key === 'requirement-spec')?.structured
        )
        setUiDesignFile(artifacts.find((artifact) => artifact.key === 'ui-design')?.structured)
      })
      .finally(() => {
        if (!cancelled) setTechnicalPlanFileLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [
    activeWorkbenchPhase,
    application.id,
    application.workspaceRoot,
    restorePlanningArtifactsFromDisk
  ])
  // 开发阶段：右侧文档区无设计阶段产物，显示引导文案（选中页面/端点后由后续逻辑填充）。
  const designDocContent = isApplicationPlanningPhase
    ? activeDesignDoc?.content || ''
    : '当前暂无设计文档。'
  const designDocName = isApplicationPlanningPhase ? activeDesignDoc?.title : undefined
  const designDocTitle = isApplicationPlanningPhase
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
    isApplicationPlanningPhase &&
    planningPhaseRunning &&
    !activeDesignDoc?.available &&
    ((activeDesignDocKey === 'requirement-spec' &&
      (planningPhase === 'requirements' || planningPhase === 'product_planning')) ||
      (activeDesignDocKey === 'technical-plan' && planningPhase === 'technical_planning'))
  // UI 设计稿生成中：UI 确认阶段 workflow running（换一换/选模板/首次生成）。
  const uiDesignGenerating =
    isDesignPhase && planningPhaseRunning && planningPhase === 'ui_confirmation'
  // acting 态的清理由 UiDesignConfirmationPanel 的 cleanup-effect（带 observedRunningRef
  // 防提前重置）全权管理；这里不再重复清理，避免与 panel 抢着清空导致下一批 acting 态
  // 在 flush 瞬间被清掉（按钮提前解禁、右侧 loading 消失）。
  const {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createTestSession,
    createReviewSession,
    createAcceptanceSession,
    discardPreparedSession,
    clearActiveSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensurePlanningSession,
    ensureRevisionDevelopmentSession,
    recoverRevisionDevelopmentSession,
    activateRevisionDevelopmentSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    openSessionForPhase,
    loadSessionIdentity,
    loadingSessions,
    messages,
    persistSession,
    selectedSkills,
    sessionError,
    sessions,
    allSessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({
    application,
    editorMode,
    workbenchPhase: activeWorkbenchPhase,
    // 开发阶段切换页面/接口目标时保留右侧面板（源码/预览/文档随选中目标自动更新），
    // 仅设计阶段切换规划会话时清空右侧文档面板。
    onCloseRightPanel: () => {
      if (isApplicationPlanningPhase) setRightPanel(undefined)
    },
    designPhasePlanning: isApplicationPlanningPhase
  })

  // DOM 源码定位仅绑定当前会话，切换页面、接口或自由会话后要求用户重新选择。
  useEffect(() => {
    setInspectedElementContext(undefined)
  }, [draftKey])

  // 当前选中页面的规划配置。必须提前到 workspaceTabs / 读取源码的 useEffect 之前，
  // 否则这些位置（尤其 useEffect 依赖数组）会在 const 暂时性死区里访问未初始化的
  // activePageOption，抛 ReferenceError: Cannot access 'activePageOption' before initialization。
  const displayedPlanningPages = developmentPlanningPages
  const displayedPlanningPageTree = developmentPlanningPageTree
  const activePageId = activeDetailTarget.type === 'page' ? activeDetailTarget.pageId : ''
  const activePageOption = useMemo(
    () => displayedPlanningPages.find((page) => page.pageId === activePageId),
    [activePageId, displayedPlanningPages]
  )
  const generatingDesignDocKey = planningPhaseRunning
    ? PHASE_DOC_KEY[String(planningPhase || '')]
    : undefined
  const workspaceTabs: WorkspaceTab[] = isApplicationPlanningPhase
    ? (designDocs || []).map((doc) => ({
        key: doc.key,
        label: doc.title,
        // 当前阶段的文档即使尚未落盘也允许切回，内容区继续展示对应 loading。
        available: doc.available || doc.key === generatingDesignDocKey
      }))
    : [
        { key: 'outline', label: '开发产物', available: true },
        { key: 'preview', label: '预览', available: Boolean(runtimePreviewBaseUrl) },
        { key: 'source', label: '源码', available: Boolean(activePageOption) },
        { key: 'doc', label: '文档', available: true },
        { key: 'stage-output', label: '阶段产物', available: true }
      ]
  const activeWorkspaceTab: WorkspaceTabKey = isApplicationPlanningPhase
    ? activeDesignDocKey ||
      designDocs?.find((doc) => doc.available)?.key ||
      designDocs?.[0]?.key ||
      'requirement-spec'
    : rightPanel?.type === 'outline'
      ? 'outline'
      : rightPanel?.type === 'preview'
        ? 'preview'
        : rightPanel?.type === 'source'
          ? 'source'
          : rightPanel?.type === 'stage-output'
            ? 'stage-output'
            : 'doc'
  const openWorkspaceTab = useCallback(
    (key: WorkspaceTabKey) => {
      if (isApplicationPlanningPhase) {
        // 已生成文档与当前生成中的文档均可切换；其他未开始的文档继续保持禁用。
        const target = designDocs?.find((doc) => doc.key === key)
        if (!target || (!target.available && target.key !== generatingDesignDocKey)) return
        setRightPanel({ type: 'doc', docKey: key as WorkspaceDocKey })
      } else if (key === 'outline') {
        setRightPanel({ type: 'outline' })
      } else if (key === 'preview') {
        setRightPanel({ type: 'preview' })
      } else if (key === 'source') {
        setRightPanel({ type: 'source' })
      } else if (key === 'doc') {
        setRightPanel({ type: 'doc' })
      }
    },
    [designDocs, generatingDesignDocKey, isApplicationPlanningPhase, setRightPanel]
  )
  // 进入开发阶段时重置右侧面板：设计阶段的 doc/docKey 布局切换为开发阶段的预览/文档。
  useEffect(() => {
    if (isApplicationPlanningPhase) return
    if (!rightPanelOpen) return
    // 设计阶段遗留的 rightPanel（带 docKey）在开发阶段无效，重置为开发产物。
    if (rightPanel?.type === 'doc' && 'docKey' in rightPanel && rightPanel.docKey) {
      setRightPanel({ type: 'outline' })
      return
    }
    // 开发阶段首次进入且右侧面板未设置：默认打开开发产物 tab。
    if (!rightPanel) {
      setRightPanel({ type: 'outline' })
    }
  }, [isApplicationPlanningPhase, rightPanelOpen, rightPanel, setRightPanel])
  // 设计阶段首次进入或文档就绪时自动打开右侧文档面板；
  // 阶段切换后如果旧 rightPanel 指向已被过滤的 UI 设计稿，也必须立即替换为当前有效产物。
  useEffect(() => {
    if (!isApplicationPlanningPhase || !rightPanelOpen) return
    const currentDocKey = rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
    // 当前文档仍属于本阶段时保留用户在同阶段内的手动选择。
    if (currentDocKey && designDocs?.some((doc) => doc.key === currentDocKey)) return
    const phaseDocKey = planningPhase ? PHASE_DOC_KEY[planningPhase] : undefined
    const phaseDoc = phaseDocKey ? designDocs?.find((doc) => doc.key === phaseDocKey) : undefined
    const firstAvailable = designDocs?.find((doc) => doc.available)
    const target = phaseDoc || firstAvailable || designDocs?.[0]
    if (target) {
      setRightPanel({ type: 'doc', docKey: target.key })
    } else if (phaseDocKey) {
      // designDocs 尚未就绪（新建工作区首帧文档未生成）：按当前阶段占住默认 tab，
      // 内容区 DocPanel 显示 loading。避免与 Effect B 同帧清空竞争导致面板不显示。
      setRightPanel({ type: 'doc', docKey: phaseDocKey })
    }
  }, [
    isApplicationPlanningPhase,
    rightPanelOpen,
    rightPanel,
    designDocs,
    planningPhase,
    setRightPanel
  ])

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
    if (!isApplicationPlanningPhase || !rightPanelOpen) return
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
  }, [
    isApplicationPlanningPhase,
    rightPanelOpen,
    planningPhase,
    uiDesignAvailable,
    rightPanel,
    setRightPanel
  ])

  // 切换应用时重置自动同步标记并清空右侧面板，避免上次会话选中的 tab 残留。
  useEffect(() => {
    lastAutoSyncedPhaseRef.current = undefined
    if (isApplicationPlanningPhase) {
      setRightPanel(undefined)
    }
  }, [application.id, isApplicationPlanningPhase, setRightPanel])

  // 切换工作区时清空上一应用的本地文档缓存，防止旧内容误放行 tab。
  useEffect(() => {
    setDesignDocFileContent({})
    setDesignDocFilePath({})
    setTechnicalPlanFile(undefined)
    setProductPlanFile(undefined)
    setRequirementSpecFile(undefined)
    setUiDesignFile(undefined)
  }, [application.id, application.workspaceRoot])

  const activeApiEndpoint = activeDetailTarget.type === 'endpoint' ? activeDetailTarget : undefined
  const activeTargetKey = detailTargetKey(activeDetailTarget)
  const planningWorkflowStatus = String(planningWorkflow?.summary?.status || '')
  // 模板就绪后创建规划已经结束；即使界面暂留在产品阶段等待“进入开发”，底部也应恢复普通自由对话。
  const designChangeWorkflowAvailable = isApplicationPlanningPhase && !lifecycleReadyForWorkbench
  const designWorkflowActive =
    designChangeWorkflowAvailable &&
    (!planningWorkflow || ACTIVE_DESIGN_WORKFLOW_STATUSES.has(planningWorkflowStatus))
  const designChangeInputLocked = designWorkflowActive && !designChangeUnlocked
  const activePreviewPath = activePageOption?.path || '/'

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
      // 验收子图完成启动后自动打开嵌入式预览，供用户直接完成验收。
      onRightPanelOpenChange(true)
      setRightPanel({ type: 'preview', requestKey: target.key, url: nextPreviewUrl })
    },
    [activePreviewPath, onRightPanelOpenChange, setRightPanel]
  )

  /** 测试会话启动时立即高亮测试阶段，避免生命周期回传延迟造成步骤条仍显示开发中。 */
  const handleEnterTestPhase = useCallback((): void => {
    switchPhase('test')
  }, [switchPhase])

  /** 审查会话创建成功后立即高亮审查阶段，避免等待代码扫描首帧才更新顶部步骤条。 */
  const handleEnterReviewPhase = useCallback((): void => {
    switchPhase('review')
  }, [switchPhase])

  /** 验收会话创建成功后立即高亮验收阶段，避免等待项目启动首帧才更新顶部步骤条。 */
  const handleEnterAcceptancePhase = useCallback((): void => {
    switchPhase('acceptance')
  }, [switchPhase])

  // 同步工作台自动启动返回的最新前端端口和错误，不进行任何浏览器持久化。
  useEffect(() => {
    setRuntimePreviewBaseUrl(previewOrigin(previewBaseUrl))
    setRuntimePreviewLaunchError(previewLaunchError)
  }, [previewBaseUrl, previewLaunchError])

  /** 为已确认 TechnicalPlan 准备本次 revision 的独立开发会话；成功前不离开规划会话。 */
  const handleRevisionContinuation = useCallback(
    (handoff: WorkflowRevisionContinuationHandoff): Promise<void> => {
      const { continuation, lifecycle } = handoff
      const inFlight = revisionDevelopmentHandoffPromisesRef.current[continuation.changeId]
      if (inFlight) return inFlight
      const promise = new Promise<void>((resolve, reject) => {
        const rejectAndRelease = (reason?: unknown): void => {
          delete revisionDevelopmentHandoffPromisesRef.current[continuation.changeId]
          reject(reason)
        }
        void (async () => {
          const activeRevision = lifecycle.activeFormalRevision
          // continuation 的来源必须由同一份 lifecycle 在完整会话列表中解析；
          // 当前 activeSession 可能被阶段切换或错误恢复会话抢占，不能作为 lineage 权威。
          const sourceSession = formalRevisionContinuationSourceSession(
            allSessions,
            lifecycle,
            application.id
          )
          const sourceIdentity = sourceSession
            ? await loadSessionIdentity(sourceSession.id)
            : undefined
          const revisionContext = bindRevisionSessionChangeId(
            sourceIdentity?.revisionContext,
            lifecycle
          )
          if (
            !sourceIdentity ||
            !revisionContext ||
            revisionContext.sessionRole !== 'design' ||
            revisionContext.changeId !== continuation.changeId ||
            revisionContext.formalBranch !== continuation.formalBranch ||
            activeRevision?.changeId !== continuation.changeId
          ) {
            throw new Error('当前需求设计会话与服务端 revision continuation 不匹配。')
          }
          const boundSourceIdentity: SessionIdentity = {
            ...sourceIdentity,
            revisionContext
          }
          const targetIdentity = await ensureRevisionDevelopmentSession(
            boundSourceIdentity,
            continuation
          )
          setPendingRevisionContinuation({
            continuation,
            reject: rejectAndRelease,
            resolve,
            sourceIdentity: boundSourceIdentity,
            targetIdentity
          })
        })().catch(rejectAndRelease)
      })
      revisionDevelopmentHandoffPromisesRef.current[continuation.changeId] = promise
      return promise
    },
    [allSessions, application.id, ensureRevisionDevelopmentSession, loadSessionIdentity]
  )

  /** 在规划来源会话持久化可重复打开的 DEVELOPMENT 交接回执。 */
  const persistRevisionDevelopmentReceipt = useCallback(
    async (
      sourceIdentity: SessionIdentity,
      targetIdentity: SessionIdentity,
      continuation: WorkflowRevisionContinuation,
      request: string
    ): Promise<void> => {
      const sourceMessages = getSessionMessages(sourceIdentity.key)
      const receiptExists = sourceMessages.some(
        (item) =>
          item.revisionHandoff?.kind === 'revision_development' &&
          item.revisionHandoff.changeId === continuation.changeId &&
          item.revisionHandoff.targetSessionId === targetIdentity.sessionId &&
          item.revisionHandoff.targetConversationThreadId === targetIdentity.threadId
      )
      if (receiptExists) return
      const receiptId = Date.now() * 1000
      const nextSourceMessages: AgentChatMessage[] = [
        ...sourceMessages,
        {
          id: receiptId,
          role: 'assistant',
          content: '',
          revisionHandoff: {
            kind: 'revision_development',
            formalBranch: continuation.formalBranch,
            targetSessionId: targetIdentity.sessionId,
            targetConversationThreadId: targetIdentity.threadId,
            impactInteractionId: sourceIdentity.revisionContext?.impactInteractionId || '',
            changeId: continuation.changeId,
            request: request.trim() || 'TechnicalPlan 已确认，进入二次修改开发阶段。'
          },
          createdAt: receiptId
        }
      ]
      setSessionMessages(sourceIdentity.key, nextSourceMessages)
      try {
        await persistSession({
          editorMode: sourceIdentity.editorMode,
          messages: nextSourceMessages,
          sessionId: sourceIdentity.sessionId,
          threadId: sourceIdentity.threadId,
          revisionContext: sourceIdentity.revisionContext,
          titleFrom: '二次修改需求设计'
        })
      } catch (error) {
        message.warning(formatError(error, '开发会话已创建，但规划会话回执保存失败'))
      }
    },
    [getSessionMessages, persistSession, setSessionMessages]
  )

  /** 在目标 DEVELOPMENT 会话持久化前置产物更新完成卡，自动执行失败后仍保留上下文。 */
  const persistRevisionDevelopmentEntry = useCallback(
    async (
      targetIdentity: SessionIdentity,
      continuation: WorkflowRevisionContinuation,
      request: string
    ): Promise<void> => {
      const targetMessages = getSessionMessages(targetIdentity.key)
      const entryExists = targetMessages.some(
        (item) =>
          item.revisionHandoff?.kind === 'revision_development_entry' &&
          item.revisionHandoff.changeId === continuation.changeId
      )
      if (entryExists) return
      const impactInteractionId = String(
        targetIdentity.revisionContext?.impactInteractionId || ''
      ).trim()
      if (!impactInteractionId) {
        throw new Error('当前开发会话缺少 revision impact 身份，无法写入交接卡。')
      }
      const entryId = Date.now() * 1000
      const retainedTargetMessages = targetMessages.filter(
        (item) =>
          !(
            item.role === 'assistant' &&
            !item.content.trim() &&
            !item.workflow &&
            !item.error &&
            !item.revisionHandoff
          )
      )
      const nextTargetMessages: AgentChatMessage[] = [
        {
          id: entryId,
          role: 'assistant',
          content: '',
          revisionHandoff: {
            kind: 'revision_development_entry',
            formalBranch: continuation.formalBranch,
            targetSessionId: targetIdentity.sessionId,
            targetConversationThreadId: targetIdentity.threadId,
            impactInteractionId,
            changeId: continuation.changeId,
            request: request.trim() || '本次需求的前置产物已更新完成。'
          },
          createdAt: entryId
        },
        ...retainedTargetMessages
      ]
      setSessionMessages(targetIdentity.key, nextTargetMessages)
      try {
        await persistSession({
          editorMode: targetIdentity.editorMode,
          messages: nextTargetMessages,
          sessionId: targetIdentity.sessionId,
          threadId: targetIdentity.threadId,
          revisionContext: targetIdentity.revisionContext,
          titleFrom: '二次修改 · 继续开发'
        })
      } catch (error) {
        // 卡片落盘失败不能阻断服务端一次性 continuation；当前会话仍保留内存卡片，
        // 自动工作区扫描和 DAG 生成继续执行。
        message.warning(formatError(error, '开发会话交接卡保存失败'))
      }
    },
    [getSessionMessages, persistSession, setSessionMessages]
  )
  // 冷恢复 formal revision 时按 change/source/planning 完整身份选择独立会话；
  // 普通首次规划才允许按固定规划 Agent 标题恢复。
  const activeRevisionStageSession = isApplicationPlanningPhase
    ? activeFormalRevisionStageSession(
        allSessions,
        applicationLifecycle,
        application.id,
        isTechnicalPlanningPhase ? 'planning' : 'product'
      )
    : undefined
  const restoredPlanningConversationThreadId = applicationLifecycle?.activeFormalRevision
    ? activeRevisionStageSession?.threadId
    : isTechnicalPlanningPhase
      ? sessions.find(
          (session) => session.workflowId === application.id && session.stage === 'PLAN'
        )?.threadId
      : undefined

  useEffect(() => {
    // formal revision 的交接恢复不依赖用户当前浏览阶段；应用直接恢复在 DEVELOPMENT
    // 时也必须补齐目标会话卡和来源回执。
    if (loadingSessions || !applicationLifecycle) return
    const active = applicationLifecycle.activeFormalRevision
    const sourceSession = formalRevisionContinuationSourceSession(
      allSessions,
      applicationLifecycle,
      application.id
    )
    const recoveryExecution = recoverableRevisionDevelopmentExecution(
      allSessions,
      applicationLifecycle,
      application.id
    )
    const technicalPlanSha256 = String(active?.technicalPlanSha256 || '').trim()
    if (
      !active ||
      !sourceSession ||
      !technicalPlanSha256 ||
      !['building', 'failed', 'stopped'].includes(String(active.status || ''))
    ) {
      return
    }
    const recoveryKey = `${active.changeId}:${technicalPlanSha256}`
    if (revisionDevelopmentRecoveryRef.current === recoveryKey) return
    revisionDevelopmentRecoveryRef.current = recoveryKey
    void (async () => {
      const sourceIdentity = await loadSessionIdentity(sourceSession.id)
      const boundContext = bindRevisionSessionChangeId(
        sourceIdentity.revisionContext,
        applicationLifecycle
      )
      if (!boundContext || boundContext.changeId !== active.changeId) {
        throw new Error('规划会话与待恢复的 development execution 身份不匹配。')
      }
      const boundSourceIdentity: SessionIdentity = {
        ...sourceIdentity,
        revisionContext: boundContext
      }
      const continuation: WorkflowRevisionContinuation = {
        changeId: active.changeId,
        formalBranch: active.formalBranch,
        action: 'continue_revision_build',
        token: 'recovery-only',
        technicalPlanSha256
      }
      // 正常路径会先创建独立 DEVELOPMENT 会话、再消费 backend continuation。
      // 冷恢复必须优先复用该可见会话；只有会话确实丢失时，才按 lifecycle execution 补建。
      const existingTarget = revisionDevelopmentSessionForContinuation(
        allSessions,
        boundSourceIdentity,
        continuation
      )
      if (!existingTarget && !recoveryExecution) {
        revisionDevelopmentRecoveryRef.current = ''
        return
      }
      const targetIdentity = existingTarget
        ? await loadSessionIdentity(existingTarget.id)
        : await recoverRevisionDevelopmentSession(
            boundSourceIdentity,
            applicationLifecycle,
            recoveryExecution!
          )
      await persistRevisionDevelopmentEntry(
        targetIdentity,
        continuation,
        String(active.request || '')
      )
      await persistRevisionDevelopmentReceipt(
        boundSourceIdentity,
        targetIdentity,
        continuation,
        String(active.request || '')
      )
    })().catch((error) => {
      if (revisionDevelopmentRecoveryRef.current === recoveryKey) {
        revisionDevelopmentRecoveryRef.current = ''
      }
      message.error(formatError(error, '恢复二次修改开发会话失败'))
    })
  }, [
    allSessions,
    application.id,
    applicationLifecycle,
    loadSessionIdentity,
    loadingSessions,
    persistRevisionDevelopmentEntry,
    persistRevisionDevelopmentReceipt,
    recoverRevisionDevelopmentSession
  ])
  // 切换到其他会话时清除“不通过后恢复对话”的局部状态，避免串用普通输入模式。
  useEffect(() => {
    if (
      acceptanceConversationSessionKey &&
      activeSession?.key &&
      activeSession.key !== acceptanceConversationSessionKey
    ) {
      setAcceptanceConversationSessionKey('')
    }
  }, [acceptanceConversationSessionKey, activeSession?.key])

  /** 创建 formal revision 的独立可见会话；后端 Graph thread 与前端 conversation thread 分离。 */
  const prepareFormalRevisionSession = useCallback(
    async (input: WorkflowDesignStageRevisionStart): Promise<SessionIdentity> => {
      const cached = formalRevisionSessionIdentitiesRef.current[input.impact.interactionId]
      if (cached) return cached
      // workbench branch 仍绑定原 planning thread 作为 lifecycle 权威身份，但实际草稿运行使用新会话 thread。
      const checkpointThreadId = String(
        planningThreadId ||
          application.planningThreadId ||
          applicationLifecycle?.activeFormalRevision?.planningThreadId ||
          applicationLifecycle?.initialization?.threadId ||
          ''
      ).trim()
      const workspaceRoot = String(application.workspaceRoot || '').trim()
      if (!checkpointThreadId || !workspaceRoot) {
        throw new Error('当前应用缺少原 planning checkpoint，无法创建独立正式修改会话。')
      }
      setGeneratingDetailTargetKey('')
      const stageEntryKey = `revision:${input.impact.formalBranch}:${input.impact.interactionId}`
      const sessionPhase = initialFormalRevisionPhase(input.impact.formalBranch)
      formalRevisionSourcePhasesRef.current[input.impact.interactionId] = activeWorkbenchPhase
      const revisionContext = createFormalRevisionSessionContext(input, checkpointThreadId)
      const sourceIdentity =
        activeSession?.sessionId === input.sourceSessionId &&
        activeSession.threadId === input.sourceConversationThreadId
          ? activeSession
          : sessionIdentityFromSummary(
              allSessions.find(
                (session) =>
                  session.workflowId === application.id &&
                  session.id === input.sourceSessionId &&
                  session.threadId === input.sourceConversationThreadId
              ),
              editorMode,
              workspaceRoot
            )
      if (!sourceIdentity) {
        throw new Error('找不到正式修改的来源会话，无法继承页面、接口或实体归属。')
      }
      const revisionIdentity = await ensurePlanningSession(
        stageEntryKey,
        sessionPhase,
        revisionContext,
        sourceIdentity
      )
      const messageBaseId = Date.now() * 1000
      // TechnicalPlan 回退的下一条 AG-UI action 会真实携带原始请求；不要先把同一请求
      // 预写成可见用户消息，否则规划会话会出现一条“模拟输入”和一条真实输入的重复卡片。
      const revisionMessages: AgentChatMessage[] =
        input.impact.formalBranch === 'workbench_plan_revision'
          ? []
          : [
              {
                id: messageBaseId,
                role: 'user',
                content: input.request,
                createdAt: messageBaseId
              }
            ]
      const sourceSessionKey = sessionRuntimeKey(workspaceRoot, editorMode, input.sourceSessionId)
      const sourceMessages = getSessionMessages(sourceSessionKey)
      const sourceReceiptExists = sourceMessages.some(
        (item) => item.revisionHandoff?.impactInteractionId === input.impact.interactionId
      )
      const nextSourceMessages = sourceReceiptExists
        ? sourceMessages
        : [
            ...sourceMessages,
            {
              id: messageBaseId + 1,
              role: 'assistant' as const,
              content: '',
              revisionHandoff: {
                kind: 'formal_revision' as const,
                formalBranch: input.impact.formalBranch,
                targetSessionId: revisionIdentity.sessionId,
                targetConversationThreadId: revisionIdentity.threadId,
                impactInteractionId: input.impact.interactionId,
                request: input.request
              },
              createdAt: messageBaseId + 1
            }
          ]
      setSessionMessages(revisionIdentity.key, revisionMessages)
      if (!sourceReceiptExists) setSessionMessages(sourceSessionKey, nextSourceMessages)
      try {
        // 先持久化目标会话，再写来源回执；回执失败时可以安全删除尚未进入的目标，
        // 避免并行写入只成功一半后无法判断哪一侧才是权威状态。
        await persistSession({
          editorMode: revisionIdentity.editorMode,
          messages: revisionMessages,
          sessionId: revisionIdentity.sessionId,
          threadId: revisionIdentity.threadId,
          revisionContext,
          titleFrom:
            input.impact.formalBranch === 'workbench_plan_revision'
              ? 'TechnicalPlan 二次修改'
              : '设计阶段二次修改'
        })
        if (!sourceReceiptExists) {
          await persistSession({
            editorMode,
            messages: nextSourceMessages,
            sessionId: input.sourceSessionId,
            threadId: input.sourceConversationThreadId
          })
        }
      } catch (error) {
        if (!sourceReceiptExists) setSessionMessages(sourceSessionKey, sourceMessages)
        // 已有来源回执时保留其目标，避免一次重复准备失败制造悬空跳转。
        if (!sourceReceiptExists) {
          try {
            await discardPreparedSession(revisionIdentity)
          } catch (rollbackError) {
            message.warning(formatError(rollbackError, '正式修改会话准备失败，预创建会话清理失败'))
          }
        }
        throw error
      }
      formalRevisionSessionIdentitiesRef.current[input.impact.interactionId] = revisionIdentity
      setLocalPlanningConversationThreadId(revisionIdentity.threadId)
      switchPhase(sessionPhase)
      return revisionIdentity
    },
    [
      application.planningThreadId,
      application.workspaceRoot,
      application.id,
      applicationLifecycle,
      activeWorkbenchPhase,
      activeSession,
      allSessions,
      discardPreparedSession,
      editorMode,
      ensurePlanningSession,
      getSessionMessages,
      persistSession,
      planningThreadId,
      setSessionMessages,
      switchPhase
    ]
  )

  /** 阶段启动失败时撤销来源回执、删除预创建会话并恢复发起阶段。 */
  const rollbackFormalRevisionSession = useCallback(
    async (
      input: WorkflowDesignStageRevisionStart | WorkflowWorkbenchPlanRevisionStart,
      identity: SessionIdentity
    ): Promise<void> => {
      const sourceSessionKey = sessionRuntimeKey(
        String(application.workspaceRoot || ''),
        editorMode,
        input.sourceSessionId
      )
      const sourceMessages = getSessionMessages(sourceSessionKey)
      const nextSourceMessages = sourceMessages.filter(
        (item) =>
          item.revisionHandoff?.impactInteractionId !== input.impact.interactionId ||
          item.revisionHandoff.targetSessionId !== identity.sessionId
      )
      try {
        if (nextSourceMessages.length !== sourceMessages.length) {
          setSessionMessages(sourceSessionKey, nextSourceMessages)
          try {
            await persistSession({
              editorMode,
              messages: nextSourceMessages,
              sessionId: input.sourceSessionId,
              threadId: input.sourceConversationThreadId
            })
          } catch (error) {
            // 回执没有成功撤销时必须保留目标会话，避免磁盘回执指向已删除会话。
            setSessionMessages(sourceSessionKey, sourceMessages)
            throw error
          }
        }
        await discardPreparedSession(identity)
      } finally {
        const sourcePhase =
          formalRevisionSourcePhasesRef.current[input.impact.interactionId] || 'development'
        delete formalRevisionSessionIdentitiesRef.current[input.impact.interactionId]
        delete formalRevisionSourcePhasesRef.current[input.impact.interactionId]
        setLocalPlanningConversationThreadId('')
        switchPhase(sourcePhase)
      }
    },
    [
      application.workspaceRoot,
      discardPreparedSession,
      editorMode,
      getSessionMessages,
      persistSession,
      setSessionMessages,
      switchPhase
    ]
  )

  /** 设计层 formal revision 创建会话后，再让原 planning Graph 恢复到权威节点。 */
  const handleStartDesignStageRevision = useCallback(
    async (input: WorkflowDesignStageRevisionStart): Promise<void> => {
      if (designRevisionStartInteractionRef.current === input.impact.interactionId) return
      designRevisionStartInteractionRef.current = input.impact.interactionId
      let revisionIdentity: SessionIdentity | undefined
      try {
        revisionIdentity = await prepareFormalRevisionSession(input)
        await onStartDesignStageRevision(input)
      } catch (error) {
        designRevisionStartInteractionRef.current = ''
        if (revisionIdentity) await rollbackFormalRevisionSession(input, revisionIdentity)
        throw error
      }
    },
    [onStartDesignStageRevision, prepareFormalRevisionSession, rollbackFormalRevisionSession]
  )

  /** TechnicalPlan-only revision 创建规划会话；主 Workflow 请求由会话 hook 使用该身份发送。 */
  const handleStartWorkbenchPlanRevision = useCallback(
    (input: WorkflowWorkbenchPlanRevisionStart): Promise<SessionIdentity> =>
      prepareFormalRevisionSession(input),
    [prepareFormalRevisionSession]
  )

  const {
    activeWorkflow,
    conversationRunning,
    error,
    handleAcceptPreview,
    handleContinueDevelopment: continueDevelopmentExecution,
    handleContinueRevisionBuild,
    handleEndPlan,
    handleResumePlan,
    handleRetryCodeReview,
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
    phaseExecution,
    sessionExecutionLocked,
    sessionRunStates,
    stopping,
    workspaceBusy
  } = useWorkflowConversation({
    acquireSessionExecution,
    activeSession,
    agUiSessionsRef,
    application,
    applicationLifecycle,
    draft,
    draftKey,
    editorMode,
    createTestSession,
    createReviewSession,
    createAcceptanceSession,
    acceptanceConversationSessionKey,
    ensureActiveSession,
    getSessionMessages,
    persistSession,
    onApplicationLifecycleChange,
    onStartDesignStageRevision: handleStartDesignStageRevision,
    onStartWorkbenchPlanRevision: handleStartWorkbenchPlanRevision,
    onRollbackFormalRevisionSession: rollbackFormalRevisionSession,
    onRevisionContinuation: handleRevisionContinuation,
    onEnterTestPhase: handleEnterTestPhase,
    onEnterReviewPhase: handleEnterReviewPhase,
    onEnterAcceptancePhase: handleEnterAcceptancePhase,
    onElementContextConsumed: (context) => {
      setInspectedElementContext((current) => (current === context ? undefined : current))
    },
    onPreviewReady: handlePreviewReady,
    publishAiMessage,
    releaseSessionExecution,
    sessionExecutions,
    selectedApiContractId: activeApiEndpoint?.apiContractId,
    selectedEndpointId: activeApiEndpoint?.endpointId,
    selectedEntityId:
      activeDetailTarget.type === 'entity' ? activeDetailTarget.entityId : undefined,
    selectedSkills,
    selectedPageId: activePageOption?.pageId || activePageOption?.key,
    selectedPageLabel: activePageOption?.label,
    inspectedElementContext,
    // 普通输入统一进入 Coordinator 对话端点，由后端自动分类意图。
    conversationEnabled: true,
    inputMode: 'conversation',
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages,
    updateSessionExecutionStatus,
    workbenchPhase: activeWorkbenchPhase
  })

  // 普通二次修改发送前清理旧的页面详细设计标记，避免历史 Workflow 触发进度卡片。
  const handleConversationSend = useCallback(
    async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
      setGeneratingDetailTargetKey('')
      await handleSend(workflowDebug)
    },
    [handleSend]
  )

  // 规划 Graph 只签发 continuation；工作台负责准备、启动并切换本次 revision 的独立开发会话。
  useEffect(() => {
    // 冷启动先等完整会话列表恢复，避免用空列表误判 revision 来源会话不存在。
    if (loadingSessions) return
    onRevisionContinuationHandlerChange(handleRevisionContinuation)
    return () => onRevisionContinuationHandlerChange(undefined)
  }, [handleRevisionContinuation, loadingSessions, onRevisionContinuationHandlerChange])

  useEffect(() => {
    if (!pendingRevisionContinuation) return
    const pending = pendingRevisionContinuation
    let targetActivated = false
    setPendingRevisionContinuation(undefined)
    void (async () => {
      // TechnicalPlan 已确认即进入本次 revision 的新开发会话；工作区扫描和 DAG 生成
      // 都必须在 DEVELOPMENT 界面可见，不能等整次 continuation 结束后才离开规划页。
      await activateRevisionDevelopmentSession(pending.targetIdentity)
      targetActivated = true
      switchPhase('development')

      // 先在目标会话落前置产物更新卡，再写来源回执并自动消费 continuation；
      // 扫描或 DAG 失败时两边都保留可恢复的用户上下文。
      const revisionRequest = String(applicationLifecycle?.activeFormalRevision?.request || '')
      await persistRevisionDevelopmentEntry(
        pending.targetIdentity,
        pending.continuation,
        revisionRequest
      )
      await persistRevisionDevelopmentReceipt(
        pending.sourceIdentity,
        pending.targetIdentity,
        pending.continuation,
        revisionRequest
      )

      const continued = await handleContinueRevisionBuild(
        pending.continuation,
        pending.targetIdentity
      )
      if (!continued) throw new Error('主 Workflow 未能接管 revision continuation。')

      pending.resolve()
    })().catch(async (error) => {
      // 尚未激活时可以清理无主预创建会话；一旦用户已进入 DEVELOPMENT，失败也必须
      // 保留当前新会话和运行记录，禁止删除后把界面再次推回规划阶段或旧开发会话。
      const sourceMessages = getSessionMessages(pending.sourceIdentity.key)
      const successfulReceiptExists = sourceMessages.some(
        (item) =>
          item.revisionHandoff?.kind === 'revision_development' &&
          item.revisionHandoff.changeId === pending.continuation.changeId &&
          item.revisionHandoff.targetSessionId === pending.targetIdentity.sessionId &&
          item.revisionHandoff.targetConversationThreadId === pending.targetIdentity.threadId
      )
      if (!targetActivated && !successfulReceiptExists) {
        try {
          await discardPreparedSession(pending.targetIdentity)
        } catch (rollbackError) {
          message.warning(formatError(rollbackError, '开发会话启动失败，预创建会话清理失败'))
        }
      }
      pending.reject(error)
    })
  }, [
    activateRevisionDevelopmentSession,
    applicationLifecycle,
    discardPreparedSession,
    getSessionMessages,
    handleContinueRevisionBuild,
    pendingRevisionContinuation,
    persistRevisionDevelopmentEntry,
    persistRevisionDevelopmentReceipt,
    switchPhase
  ])
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

  // 创建规划阶段：激活当前阶段的前端聊天会话，并注册原 Graph 的流式注入句柄，
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
  // 记录最近一次见到的 UI 确认阶段 workflow runId。UI 确认阶段轮询 no-op resume 时，
  // 后端会先流式发出 summary.message（"UI设计稿已生成…"）作为纯 content，再发 workflow
  // 快照。纯 content 到达时 lastMessage 可能已无 workflow（被前一次 content 合并污染），
  // 导致 lastIsUiDesignConfirmationCard 判断失效、新增无 workflow 的 content 消息卡片，
  // 之后所有轮询 chunk 都因 lastMessage 无 workflow 而重复新增卡片。用此 ref 标记"当前
  // 处于 UI 确认阶段"，纯 content 分支据此丢弃轮询 content，只让 workflow 快照更新卡片。
  const lastUiDesignRunIdRef = useRef<string | undefined>(undefined)
  // 二次修改 TechnicalPlan 确认只是在原 checkpoint 上签发开发 continuation；
  // 期间不展示 technical_planning 的 resume 过渡帧，避免误导为再次生成技术规划。
  const suppressRevisionTechnicalPlanTransitionRef = useRef(false)
  // 规划消息自增 id 计数器，避免同一毫秒内新增多条消息导致 React key 重复。
  const planningMessageIdRef = useRef(0)
  // 按入口门禁锁定设计到规划的原地交接；新 revision 的新门禁不能复用旧锁。
  const planningStageTransitionRef = useRef('')
  // 用 ref 持有最新的 session 操作函数，避免 effect 依赖它们导致循环。
  const getSessionMessagesRef = useRef(getSessionMessages)
  const setSessionMessagesRef = useRef(setSessionMessages)
  const activeSessionIdRef = useRef(activeSessionId)
  const persistSessionRef = useRef(persistSession)
  const activeSessionRef = useRef(activeSession)
  const applicationLifecycleRef = useRef(applicationLifecycle)
  getSessionMessagesRef.current = getSessionMessages
  setSessionMessagesRef.current = setSessionMessages
  activeSessionIdRef.current = activeSessionId
  persistSessionRef.current = persistSession
  activeSessionRef.current = activeSession
  applicationLifecycleRef.current = applicationLifecycle
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
      const currentMessages = getSessionMessagesRef.current(sessionKey)
      const msgs = compactPlanningMessageHistory(currentMessages, planningWorkflowRef.current)
      if (!msgs.length) return
      if (msgs !== currentMessages) {
        setSessionMessagesRef.current(sessionKey, msgs)
      }
      void persistSessionRef
        .current({
          editorMode: identity.editorMode,
          messages: msgs,
          sessionId: identity.sessionId,
          threadId: identity.threadId,
          revisionContext: bindRevisionSessionChangeId(
            identity.revisionContext,
            applicationLifecycleRef.current
          ),
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
  const appendPlanningUserMessage = useCallback(
    (answers?: WorkflowClarificationAnswers, withLoadingPlaceholder = true) => {
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
        ...(withLoadingPlaceholder
          ? [
              {
                id: assistantPlaceholderId,
                role: 'assistant' as const,
                content: '',
                planningLoading: true,
                createdAt: assistantPlaceholderId
              }
            ]
          : [])
      ])
    },
    []
  )

  // 把一条流式 chunk 注入指定 session 的 messages（构造/更新 assistant 消息）。
  // 同一轮规划的流式 chunk 更新最后一条 assistant 消息；
  // 新一轮（用户提交确认后，或 runId 变化）新增消息卡片，保留历史对话。
  const injectPlanningChunk = (
    sessionKey: string,
    chunk: { content?: string; workflow?: WorkflowRunPayload }
  ): void => {
    if (
      shouldSuppressConfirmedTechnicalPlanTransitionChunk(
        chunk.workflow,
        suppressRevisionTechnicalPlanTransitionRef.current
      )
    ) {
      return
    }
    // 非过渡帧代表确认失败、重新进入待确认或流程已经离开 technical_planning；
    // 立即恢复普通消息投影，确保真实错误和后续生成进度仍可见。
    if (suppressRevisionTechnicalPlanTransitionRef.current && chunk.workflow) {
      suppressRevisionTechnicalPlanTransitionRef.current = false
    }
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
    // 创建规划的确认卡（需求/产品/UI/阶段入口/技术）在轮询或 resume 时会触发
    // 新 runId（每次 resume 都 randomUUID），且中途流式快照可能丢失 clarification.mode。
    // 只要最后一条消息与本次快照是同一种确认卡、且未显式开启新轮，就视为同轮更新，
    // 更新同一张卡片而非新增——否则每次轮询都会新建一张同 phase 的确认卡并残留 loading。
    const lastClarificationMode = lastMessage?.workflow?.summary?.clarification as
      | { mode?: string }
      | undefined
    const chunkClarificationMode = chunk.workflow?.summary?.clarification as
      | { mode?: string }
      | undefined
    const lastMode = lastClarificationMode?.mode
    const chunkMode = chunkClarificationMode?.mode
    const sameDesignConfirmation =
      !planningNewRoundRef.current &&
      lastMessage?.role === 'assistant' &&
      Boolean(lastMode) &&
      PLANNING_CONFIRMATION_MODES.has(String(lastMode)) &&
      // 中途流式快照可能丢失 mode（undefined），此时沿用最后一张确认卡的判定；
      // 只有 chunk 明确带了不同 mode 才不算同轮。
      (!chunkMode || chunkMode === lastMode)
    // UI 设计稿确认阶段的轮询 no-op resume 每次产生新 runId，且中途流式快照可能
    // 丢失 clarification.mode，导致 sameDesignConfirmation 失效、走"新一轮"分支
    // 每次新增一张"UI设计稿已生成"消息卡片。phase=ui_confirmation 在 running 期间
    // 不丢失，用它作为权威判据：最后一条已是 UI 确认卡时，同阶段 chunk 视为同轮更新，
    // 只刷新卡片页面状态，不新增消息。
    const lastPhase = lastMessage?.workflow?.summary?.phase
    const chunkPhase = chunk.workflow?.summary?.phase
    const sameUiDesignPhase =
      !planningNewRoundRef.current &&
      lastMessage?.role === 'assistant' &&
      Boolean(lastWorkflowRunId) &&
      lastPhase === 'ui_confirmation' &&
      (!chunkPhase || chunkPhase === 'ui_confirmation')

    if (chunk.workflow) {
      const incomingWorkflow = chunk.workflow
      // 记录最近一次 UI 确认阶段 workflow 的 runId，供纯 content 分支识别轮询 content。
      if (incomingWorkflow.summary?.phase === 'ui_confirmation') {
        lastUiDesignRunIdRef.current = incomingWorkflow.runId
      }
      // 新一轮 content 先于 workflow 到达时，content 已新增为无 workflow 的消息；
      // workflow 到达时应合并到该消息，而非再新增一条。
      const mergeIntoContentOnly =
        !sameRun &&
        !sameDesignConfirmation &&
        !sameUiDesignPhase &&
        lastMessage?.role === 'assistant' &&
        !lastWorkflowRunId &&
        (planningNewRoundRef.current || currentMessages.length > 0)
      if (sameRun || sameDesignConfirmation || sameUiDesignPhase || mergeIntoContentOnly) {
        // 同一轮或合并 content-only 消息：更新最后一条消息的 workflow 与 content。
        planningNewRoundRef.current = false
        // 判断该 chunk 是否已经携带可见状态。首次创建沿用原有阶段判定；
        // 只有设计变更轮次才由聊天活动块承接 requirements/UI 等实时进度。
        const chunkSettlesLoading = planningWorkflowSettlesLoading(incomingWorkflow)
        const chunkHasContent = Boolean(chunk.content?.trim())
        const chunkActivity = planningWorkflowActivity(incomingWorkflow)
        const chunkIsPlanningRunning =
          incomingWorkflow.summary?.status === 'running' &&
          (['product_planning', 'project_planning', 'technical_planning'].includes(
            String(incomingWorkflow.summary?.phase || '')
          ) ||
            chunkActivity?.status === 'running')
        const hasSubstantiveWorkflow =
          chunkSettlesLoading || chunkHasContent || chunkIsPlanningRunning
        setSessionMessagesRef.current(sessionKey, (prev) => {
          const updated = [...prev]
          const prevMessage = updated[updated.length - 1]
          updated[updated.length - 1] = {
            ...prevMessage,
            workflow: retainApplicationPlanningInterrupt(prevMessage.workflow, incomingWorkflow),
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
        const requiresInput = planningWorkflowRequiresUserInput(incomingWorkflow)
        const chunkActivity = planningWorkflowActivity(incomingWorkflow)
        const isPlanningRunning =
          incomingWorkflow.summary?.status === 'running' &&
          (['product_planning', 'project_planning', 'technical_planning'].includes(
            String(incomingWorkflow.summary?.phase || '')
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
            workflow: incomingWorkflow,
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
      // UI 确认阶段轮询 no-op resume 会先流式发出 summary.message（"UI设计稿已生成…"）
      // 作为纯 content，再发 workflow 快照。轮询每次产生新 runId，且中途 workflow 快照的
      // phase 可能为 null、lastMessage 的 workflow 可能残缺，导致 lastIsUiDesignConfirmationCard
      // 和基于 lastMessage 的判断全部失效，纯 content 被放行新增无 workflow 卡片，之后所有
      // 轮询 chunk 都因 lastMessage 无 workflow 而重复新增。这里用 lastUiDesignRunIdRef 兜底：
      // 只要最近见过 UI 确认阶段 workflow 且未显式开启新轮，就认定纯 content 是 UI 轮询的
      // summary.message 噪音，直接丢弃，只让 workflow 快照更新卡片。不依赖 lastMessage 的
      // phase/runId——它们在轮询期间不可靠。
      const uiPollContentGuard =
        !planningNewRoundRef.current && Boolean(lastUiDesignRunIdRef.current)
      if (uiPollContentGuard) return
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
      // UI 确认卡片的轮询 text/delta（如「还有 N 个问题需要补充」）会短暂替代卡片造成闪烁。
      // 最后一条消息已是 UI 确认卡时直接丢弃纯 content，卡片持续显示不闪烁。
      const lastIsUiDesignConfirmationCard =
        lastMessage?.role === 'assistant' &&
        Boolean(lastWorkflowRunId) &&
        (lastMessage?.workflow?.summary?.phase === 'ui_confirmation' ||
          (lastMessage?.workflow?.summary?.clarification as { mode?: string } | undefined)?.mode ===
            'ui_design_confirmation')
      if (lastIsUiDesignConfirmationCard) return
      const appendToLast =
        lastMessage?.role === 'assistant' &&
        (lastIsPlaceholder ||
          lastIsStableWorkflowCard ||
          (!planningNewRoundRef.current &&
            (sameDesignConfirmation ||
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
  const activeFormalRevision = applicationLifecycle?.activeFormalRevision
  const formalRevisionPlanningActive = Boolean(
    activeFormalRevision &&
      ((activeFormalRevision.formalBranch === 'design_stage_revision' &&
        ['design_planning', 'continuation_ready'].includes(activeFormalRevision.status)) ||
        (activeFormalRevision.formalBranch === 'workbench_plan_revision' &&
          ['drafting', 'awaiting_user', 'continuation_ready'].includes(
            activeFormalRevision.status
          )))
  )
  const activePlanningConversationThreadId = localPlanningConversationThreadId
  const businessPlanningSessionActive = Boolean(
    (formalRevisionPlanningActive &&
      activeFormalRevision &&
      activeWorkbenchPhase === derivedWorkbenchPhase) ||
      (applicationLifecycle &&
        applicationLifecycle.initialization.stage !== 'ready_for_workbench' &&
        activeWorkbenchPhase === derivedWorkbenchPhase)
  )
  const planningSessionLookupKey =
    activePlanningConversationThreadId ||
    restoredPlanningConversationThreadId ||
    (formalRevisionPlanningActive ? undefined : planningThreadId)
  const planningSessionPhase = isTechnicalPlanningPhase ? 'planning' : 'product'
  const existingPlanningSession = useMemo(
    () =>
      allSessions.find(
        (session) =>
          session.workflowId === application.id &&
          session.workbenchPhase === planningSessionPhase &&
          (session.entryKey === planningSessionLookupKey ||
            session.threadId === planningSessionLookupKey)
      ),
    [allSessions, application.id, planningSessionLookupKey, planningSessionPhase]
  )
  const existingPlanningSessionThreadId = existingPlanningSession?.threadId
  const pendingDagExecution = useMemo(
    () => pendingDagConfirmationExecution(applicationLifecycle),
    [applicationLifecycle]
  )
  const [pendingDagSessionId, setPendingDagSessionId] = useState('')
  const pendingDagSession = useMemo(
    () => allSessions.find((session) => session.id === pendingDagSessionId),
    [allSessions, pendingDagSessionId]
  )
  const pendingDagSessionIdentity = useMemo(
    () =>
      sessionIdentityFromSummary(pendingDagSession, editorMode, application.workspaceRoot || ''),
    [application.workspaceRoot, editorMode, pendingDagSession]
  )
  const pendingDagSessionMessages = useMemo(
    () =>
      pendingDagSessionIdentity ? getSessionMessages(pendingDagSessionIdentity.key) : [],
    [getSessionMessages, pendingDagSessionIdentity]
  )
  const pendingDagWorkflow = pendingDagConfirmationWorkflow(
    pendingDagSessionMessages,
    pendingDagExecution
  )

  // lifecycle 只保存 Graph 身份；冷启动时按 run/thread 精确扫描持久化会话，恢复确认卡来源。
  useEffect(() => {
    if (!pendingDagExecution) {
      setPendingDagSessionId('')
      return
    }
    if (pendingDagWorkflow && pendingDagSessionId) return
    let cancelled = false

    /** 逐条装载当前应用会话，直到找到持有待确认 Workflow 的原始会话。 */
    const restorePendingDagSession = async (): Promise<void> => {
      const candidates = allSessions.filter((session) => session.workflowId === application.id)
      for (const candidate of candidates) {
        const identity = sessionIdentityFromSummary(
          candidate,
          editorMode,
          application.workspaceRoot || ''
        )
        if (!identity) continue
        let workflow = pendingDagConfirmationWorkflow(
          getSessionMessages(identity.key),
          pendingDagExecution
        )
        if (!workflow) {
          const loadedIdentity = await loadSessionIdentity(candidate.id)
          workflow = pendingDagConfirmationWorkflow(
            getSessionMessages(loadedIdentity.key),
            pendingDagExecution
          )
        }
        if (cancelled) return
        if (workflow) {
          setPendingDagSessionId(candidate.id)
          return
        }
      }
    }

    void restorePendingDagSession()
    return () => {
      cancelled = true
    }
  }, [
    allSessions,
    application.id,
    application.workspaceRoot,
    editorMode,
    getSessionMessages,
    loadSessionIdentity,
    pendingDagExecution,
    pendingDagSessionId,
    pendingDagWorkflow
  ])
  const planningSessionRunActive = isApplicationPlanningPhase && planningPhaseRunning
  const planningRunLockedByOtherSession = Boolean(
    planningSessionRunActive &&
      (!existingPlanningSession || existingPlanningSession.id !== activeSessionId)
  )
  const pendingDagLockedByOtherSession = Boolean(
    pendingDagExecution && (!pendingDagSessionId || pendingDagSessionId !== activeSessionId)
  )
  const otherSessionExecutionLocked =
    sessionExecutionLocked || planningRunLockedByOtherSession || pendingDagLockedByOtherSession
  const phaseSessionRunActive =
    Boolean(phaseExecution) || planningSessionRunActive || Boolean(pendingDagExecution)
  const phaseExecutionSessionTitle = phaseExecution
    ? allSessions.find((session) => session.id === phaseExecution.identity.sessionId)?.title
    : pendingDagSession?.title || existingPlanningSession?.title
  const phaseExecutionStatus =
    phaseExecution?.status || (pendingDagExecution ? 'awaiting_user' : 'running')
  const workflowInputLocked = workspaceBusy || Boolean(pendingDagExecution)
  const displayedSessionRunStates =
    planningSessionRunActive && existingPlanningSession
      ? { ...sessionRunStates, [existingPlanningSession.id]: 'running' as const }
      : sessionRunStates
  const planningSessionHistoryReady = Boolean(
    !isApplicationPlanningPhase ||
      !planningSessionLookupKey ||
      (activeSession?.workflowId === application.id &&
        (activeSession.entryKey === planningSessionLookupKey ||
          activeSession.threadId === planningSessionLookupKey))
  )

  // 首次进入工作台时同时等待会话列表、消息正文和设计阶段规划会话激活，避免遮罩结束后短暂显示空对话。
  useEffect(() => {
    onSessionHistoryReadyChange(!loadingSessions && planningSessionHistoryReady)
  }, [loadingSessions, onSessionHistoryReadyChange, planningSessionHistoryReady])

  useEffect(() => {
    // 正式二次修改只能恢复其独立前端会话；匹配失败时禁止退回原 Graph thread，
    // 否则会把新的修改消息写入初次需求设计的可见会话。
    if (!isApplicationPlanningPhase || !planningSessionLookupKey) return
    // 等 session 列表加载完再 ensure，避免 sessionSummaries 为空时找不到已有 session
    // 而创建新 session，导致历史对话丢失。
    if (loadingSessions) return
    // 冷启动 lifecycle 尚未校准时不能凭持久化阶段和原 Graph thread 补建普通规划会话。
    if (!applicationLifecycle) return
    const formalRevisionSession =
      activeRevisionStageSession ||
      (existingPlanningSession?.revisionContext?.kind === 'formal_revision'
        ? existingPlanningSession
        : undefined)
    const formalRevisionSessionIdentity = formalRevisionSession
      ? sessionIdentityFromSummary(
          formalRevisionSession,
          editorMode,
          String(application.workspaceRoot || '')
        )
      : undefined
    const formalRevisionContext = bindRevisionSessionChangeId(
      formalRevisionSessionIdentity?.revisionContext,
      applicationLifecycle
    )
    // active formal revision 只能恢复完整身份匹配的会话；缺失时等待会话列表刷新，
    // 禁止用 Graph thread 或普通 workflow 会话补建没有 revisionContext 的替代会话。
    if (activeFormalRevision && (!formalRevisionSessionIdentity || !formalRevisionContext)) return
    // 顶部阶段栏只负责浏览：非业务规划期间只能打开既有会话，禁止因 phase 切换补建会话。
    if (!businessPlanningSessionActive && !existingPlanningSessionThreadId) return
    const sessionLookupKey =
      formalRevisionSessionIdentity?.threadId ||
      existingPlanningSessionThreadId ||
      planningSessionLookupKey
    let cancelled = false
    void ensurePlanningSessionRef
      .current(
        sessionLookupKey,
        planningSessionPhase,
        formalRevisionContext,
        formalRevisionSessionIdentity
      )
      .then((identity) => {
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
        // 规划会话回放完缓存后仍无消息时注入即时占位，避免只显示 Agent 头像。
        const currentMsgs = getSessionMessagesRef.current(identity.key)
        if (currentMsgs.length === 0) {
          const placeholderId = Date.now() * 1000 + (planningMessageIdRef.current++ % 1000)
          setSessionMessagesRef.current(identity.key, (messages) =>
            appendPlanningLoadingPlaceholder(messages, {
              id: placeholderId,
              role: 'assistant',
              content: '',
              planningLoading: true,
              createdAt: placeholderId
            })
          )
        }
        // 工作台或会话键晚于最终 AG-UI 帧就绪时，用外层保存的权威快照收口占位消息。
        const latestPlanningWorkflow = planningWorkflowRef.current
        const stalePlanningEntry =
          isTechnicalPlanningPhase &&
          planningWorkflowPhase(latestPlanningWorkflow) === 'planning_stage_entry'
        if (
          !stalePlanningEntry &&
          shouldBackfillPlanningWorkflow(latestPlanningWorkflow, planningNewRoundRef.current)
        ) {
          injectPlanningChunk(identity.key, { workflow: latestPlanningWorkflow })
        }
      })
    return () => {
      cancelled = true
    }
    // 只依赖创建规划阶段、threadId 和会话加载态，ensurePlanningSession 用 ref 避免循环。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    isApplicationPlanningPhase,
    isTechnicalPlanningPhase,
    loadingSessions,
    businessPlanningSessionActive,
    planningSessionLookupKey,
    planningSessionPhase,
    existingPlanningSessionThreadId,
    existingPlanningSession,
    activeRevisionStageSession,
    activeFormalRevision,
    applicationLifecycle,
    application.workspaceRoot,
    editorMode
  ])

  // 外层规划状态始终保留最新 Workflow；稳定快照到达时主动补齐可能漏掉的流式确认卡。
  useEffect(() => {
    const sessionKey = planningSessionKeyRef.current
    if (
      !isApplicationPlanningPhase ||
      !planningThreadId ||
      !sessionKey ||
      (isTechnicalPlanningPhase &&
        planningWorkflowPhase(planningWorkflow) === 'planning_stage_entry') ||
      !shouldBackfillPlanningWorkflow(planningWorkflow, planningNewRoundRef.current)
    ) {
      return
    }
    injectPlanningChunk(sessionKey, { workflow: planningWorkflow })
    // injectPlanningChunk 读取的会话操作均由 ref 保持最新，避免把函数身份加入依赖造成重复注入。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isApplicationPlanningPhase, isTechnicalPlanningPhase, planningThreadId, planningWorkflow])

  useEffect(() => {
    if (!onPlanningStreamReady) return
    // 不依赖创建规划阶段：工作台刚进入时 lifecycle 尚未加载，阶段推导可能尚未就绪，
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
  // 新建对话的空白草稿不归属于任何历史 Run；应用级 execution 不能重新锁住输入区。
  const detachedConversationDraft =
    !isApplicationPlanningPhase && !activeSession && activeDetailTarget.type === 'none'
  const displayedPlanExecutionMode =
    detachedConversationDraft || planEnded
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
        : Boolean(activePageOption?.designed || activePageOption?.hasDetailPlan),
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
  const currentStageSessionTargetKey = workflowDetailTargetKey(latestWorkflowForDisplay)
  const stageOutputContextAligned = activeTargetKey
    ? currentStageSessionTargetKey === activeTargetKey
    : !currentStageSessionTargetKey
  const stageOutputMessages = useMemo(
    () =>
      pendingDagExecution
        ? pendingDagSessionMessages
        : stageOutputContextAligned
          ? messages
          : [],
    [pendingDagExecution, pendingDagSessionMessages, stageOutputContextAligned, messages]
  )
  const stageOutputWorkflow = pendingDagExecution
    ? pendingDagWorkflow
    : stageOutputContextAligned
      ? latestWorkflowForDisplay
      : undefined
  const currentDagSnapshot = useMemo(
    () => latestDagGenerationSnapshot(stageOutputMessages),
    [stageOutputMessages]
  )
  const dagConfirmationPlan = useMemo(
    () => currentDagConfirmationPlan(stageOutputWorkflow),
    [stageOutputWorkflow]
  )
  const dagConfirmationTargetReview = useMemo(
    () => currentDagConfirmationTargetReview(stageOutputWorkflow),
    [stageOutputWorkflow]
  )
  const [dagConfirmationSubmissionError, setDagConfirmationSubmissionError] = useState('')
  useEffect(() => {
    setDagConfirmationSubmissionError('')
  }, [pendingDagExecution?.runId])
  const dagConfirmationErrors = useMemo(
    () => [
      ...currentDagConfirmationErrors(stageOutputWorkflow),
      ...(dagConfirmationSubmissionError ? [dagConfirmationSubmissionError] : [])
    ],
    [dagConfirmationSubmissionError, stageOutputWorkflow]
  )
  const currentStageOutputPhase = stageOutputPhase(
    stageOutputWorkflow,
    currentDagSnapshot,
    dagConfirmationPlan
  )
  const currentRunningDagStage = runningDagGenerationStage(currentDagSnapshot)
  const stageOutputSessionKey = pendingDagExecution
    ? `${application.id}:pending-dag:${pendingDagExecution.runId}`
    : `${application.id}:${activeTargetKey || 'free-chat'}:${activeSession?.key || draftKey}`
  const stageOutputMatchesSession =
    rightPanel?.type === 'stage-output' && rightPanel.sessionKey === stageOutputSessionKey
  const selectedDagStage = selectedDagGenerationStage(
    currentDagSnapshot,
    stageOutputMatchesSession && rightPanel?.type === 'stage-output'
      ? rightPanel.stageId
      : undefined
  )
  const activeDagStageId =
    stageOutputMatchesSession && rightPanel?.type === 'stage-output' && rightPanel.view === 'stage'
      ? rightPanel.stageId
      : undefined
  const manuallySelectedDagSessionRef = useRef('')
  const lastStageOutputSessionRef = useRef('')
  const lastStageOutputPhaseRef = useRef('')
  const lastPinnedDagRunRef = useRef('')

  // 右侧阶段产物只在切换会话或进入新的大阶段时自动跟随；同一 DAG 生成阶段内，
  // 用户点选过其它子阶段后，后续流式快照只能更新内容，不能抢回当前选择。
  useEffect(() => {
    if (isApplicationPlanningPhase) return
    if (pendingDagExecution) {
      // 每个待确认运行首次出现时自动打开确认卡；之后允许用户查看其它工作区 tab，
      // 但再次进入阶段产物时仍始终读取这个运行的原会话与确认版本。
      const enteringPendingRun = lastPinnedDagRunRef.current !== pendingDagExecution.runId
      lastPinnedDagRunRef.current = pendingDagExecution.runId
      if (enteringPendingRun) {
        if (!rightPanelOpen) onRightPanelOpenChange(true)
        setRightPanel({
          type: 'stage-output',
          sessionKey: stageOutputSessionKey,
          view: 'confirmation'
        })
      } else if (
        rightPanel?.type === 'stage-output' &&
        (rightPanel.sessionKey !== stageOutputSessionKey || rightPanel.view !== 'confirmation')
      ) {
        setRightPanel({
          type: 'stage-output',
          sessionKey: stageOutputSessionKey,
          view: 'confirmation'
        })
      }
      return
    }
    lastPinnedDagRunRef.current = ''
    const sessionChanged = lastStageOutputSessionRef.current !== stageOutputSessionKey
    const phaseChanged = lastStageOutputPhaseRef.current !== currentStageOutputPhase

    if (sessionChanged || phaseChanged) {
      lastStageOutputSessionRef.current = stageOutputSessionKey
      lastStageOutputPhaseRef.current = currentStageOutputPhase
      manuallySelectedDagSessionRef.current = ''

      if (currentStageOutputPhase === 'confirmation') {
        if (!rightPanelOpen) onRightPanelOpenChange(true)
        setRightPanel({
          type: 'stage-output',
          sessionKey: stageOutputSessionKey,
          view: 'confirmation'
        })
        return
      }
      if (currentStageOutputPhase === 'generation') {
        if (!rightPanelOpen) onRightPanelOpenChange(true)
        setRightPanel({
          type: 'stage-output',
          sessionKey: stageOutputSessionKey,
          view: 'stage',
          stageId: currentRunningDagStage?.id
        })
        return
      }
      if (rightPanel?.type === 'stage-output') {
        setRightPanel({ type: 'stage-output', sessionKey: stageOutputSessionKey })
      }
      return
    }

    if (!rightPanelOpen) return
    if (
      currentStageOutputPhase === 'generation' &&
      manuallySelectedDagSessionRef.current !== stageOutputSessionKey &&
      rightPanel?.type === 'stage-output' &&
      rightPanel.sessionKey === stageOutputSessionKey &&
      (rightPanel.view !== 'stage' || rightPanel.stageId !== currentRunningDagStage?.id)
    ) {
      setRightPanel({
        type: 'stage-output',
        sessionKey: stageOutputSessionKey,
        view: 'stage',
        stageId: currentRunningDagStage?.id
      })
    }
  }, [
    currentRunningDagStage?.id,
    currentStageOutputPhase,
    isApplicationPlanningPhase,
    onRightPanelOpenChange,
    pendingDagExecution,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    stageOutputSessionKey
  ])

  /** 从中间 DAG 卡片选择子阶段；点击运行中阶段时恢复自动跟随。 */
  const handleDagStageSelect = useCallback(
    (stageId: string): void => {
      const target = selectedDagGenerationStage(currentDagSnapshot, stageId)
      if (!target || (!target.output && target.status !== 'running')) return
      manuallySelectedDagSessionRef.current =
        target.status === 'running' ? '' : stageOutputSessionKey
      onRightPanelOpenChange(true)
      setRightPanel({
        type: 'stage-output',
        sessionKey: stageOutputSessionKey,
        view: 'stage',
        stageId
      })
    },
    [currentDagSnapshot, onRightPanelOpenChange, setRightPanel, stageOutputSessionKey]
  )

  /** 从确认阶段的历史子阶段产物返回当前任务确认卡。 */
  const handleReturnDagConfirmation = useCallback((): void => {
    manuallySelectedDagSessionRef.current = ''
    setRightPanel({
      type: 'stage-output',
      sessionKey: stageOutputSessionKey,
      view: 'confirmation'
    })
  }, [setRightPanel, stageOutputSessionKey])
  const activeWorkflowPhase = String(
    activeWorkflow?.summary?.phase ||
      activeWorkflow?.result?.phase ||
      activeWorkflow?.state?.phase ||
      ''
  )
  const {
    available: testReportAvailable,
    content: testReportContent,
    error: testReportError,
    loading: testReportLoading,
    path: testReportPath
  } = useTestReportPanel({
    activeWorkflowPhase,
    applicationId: application.id,
    isApplicationPlanningPhase,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    workflow: latestWorkflowForDisplay,
    workspaceRoot: application.workspaceRoot
  })
  const {
    available: reviewReportAvailable,
    content: reviewReportContent,
    error: reviewReportError,
    loading: reviewReportLoading,
    path: reviewReportPath
  } = useCodeReviewReportPanel({
    activeWorkflowPhase,
    applicationId: application.id,
    isApplicationPlanningPhase,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    workflow: latestWorkflowForDisplay,
    workspaceRoot: application.workspaceRoot
  })
  const codeDiffVisible = workflowShouldShowCodeChanges(latestWorkflowForDisplay)

  // 阶段切入测试或审查后立即退出可能遗留的 Diff 详情，避免卡片隐藏后右侧仍显示代码差异。
  useEffect(() => {
    if (!rightPanelOpen || rightPanel?.type !== 'diff' || codeDiffVisible) return
    if (reviewReportAvailable) {
      setRightPanel({ type: 'review-report' })
      return
    }
    setRightPanel(testReportAvailable ? { type: 'test-report' } : { type: 'doc' })
  }, [
    codeDiffVisible,
    reviewReportAvailable,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    testReportAvailable
  ])
  const displayedWorkspaceTabs: WorkspaceTab[] = isApplicationPlanningPhase
    ? workspaceTabs
    : [
        ...workspaceTabs,
        { key: 'test-report', label: '测试报告', available: testReportAvailable },
        { key: 'review-report', label: '审查报告', available: reviewReportAvailable }
      ]
  const displayedWorkspaceTab: WorkspaceTabKey =
    !isApplicationPlanningPhase && rightPanel?.type === 'test-report'
      ? 'test-report'
      : !isApplicationPlanningPhase && rightPanel?.type === 'review-report'
        ? 'review-report'
        : activeWorkspaceTab
  /** 在开发阶段处理阶段产物及测试/审查报告 tab，其余 tab 继续复用既有切换逻辑。 */
  const openDisplayedWorkspaceTab = useCallback(
    (key: WorkspaceTabKey): void => {
      if (key === 'stage-output' && !isApplicationPlanningPhase) {
        manuallySelectedDagSessionRef.current = ''
        setRightPanel({
          type: 'stage-output',
          sessionKey: stageOutputSessionKey,
          view:
            currentStageOutputPhase === 'confirmation'
              ? 'confirmation'
              : currentStageOutputPhase === 'generation'
                ? 'stage'
                : undefined,
          stageId: currentStageOutputPhase === 'generation' ? currentRunningDagStage?.id : undefined
        })
        return
      }
      if (key === 'test-report' && !isApplicationPlanningPhase) {
        if (testReportAvailable) setRightPanel({ type: 'test-report' })
        return
      }
      if (key === 'review-report' && !isApplicationPlanningPhase) {
        if (reviewReportAvailable) setRightPanel({ type: 'review-report' })
        return
      }
      openWorkspaceTab(key)
    },
    [
      currentRunningDagStage?.id,
      currentStageOutputPhase,
      isApplicationPlanningPhase,
      openWorkspaceTab,
      reviewReportAvailable,
      setRightPanel,
      stageOutputSessionKey,
      testReportAvailable
    ]
  )
  const conversationActive = conversationRunning || isConversationWorkflow(latestWorkflowForDisplay)
  const acceptanceAwaiting = displayedPlanExecutionMode === 'awaiting_acceptance'
  const activeSessionTargetKey = currentStageSessionTargetKey
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
    // 新一轮运行尚未收到实时 Workflow 时，不能使用历史消息中的旧快照显示页面进度。
    Boolean(activeWorkflow) &&
    activeWorkflowMatchesTarget &&
    (generatingDetailTargetKey === activeTargetKey ||
      activeWorkflowPhase === 'entity_source_binding') &&
    developmentPlanningReady &&
    Boolean(activeApiEndpoint || activePageOption) &&
    !detailConfirmationWaitingReview
  const activeSessionUpdatedAt = sessions.find(
    (session) => session.id === activeSessionId
  )?.updatedAt
  const entityDetailTarget = activeDetailTarget.type === 'entity' ? activeDetailTarget : undefined
  // 当前目标启动阶段会话后直接展示设计对话；已设计且无活动会话时展示信息面板（查看设计）；
  // 未设计实体与页面/接口保持一致，由锁定引导卡片接管，避免直接落入对话区。
  const entitySessionActive = Boolean(entityDetailTarget && activeSession)
  const endpointSessionActive = Boolean(activeApiEndpoint && activeSession)
  const showEndpointDetailDesignEntry = shouldShowEndpointDetailDesignEntry(
    activeApiEndpointOption?.endpoint,
    endpointSessionActive,
    messages.length
  )
  const showPageDetailDesignEntry = Boolean(
    activeDetailTarget.type === 'page' && shouldShowPageDetailDesignEntry(activePageOption, false)
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

  // 实体设计确认后，恢复原开发目标的展示；续接卡由运行完成边界负责持久化。
  // 直接从大纲进入实体设计时仍回到实体信息页。设计过程中的每次动作都是一次
  // requires_user_input 子 run，不能据此触发续接或清空当前会话。
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
    const continuation = workflowDevelopmentContinuation(
      workflow,
      displayedPlanningPages,
      developmentPlanningApiContracts
    )
    if (continuation && activeSession) {
      if (continuation.target.type === 'page') {
        setActiveDetailTarget({ type: 'page', pageId: continuation.target.pageId })
        setInteractingDetailTargetKey(pageDetailTargetKey(continuation.target.pageId))
      } else {
        setActiveDetailTarget({
          type: 'endpoint',
          apiContractId: continuation.target.apiContractId,
          endpointId: continuation.target.endpointId,
          endpointKey: `${continuation.target.apiContractId}:${continuation.target.endpointId}`,
          label: continuation.target.label
        })
        setInteractingDetailTargetKey(
          endpointDetailTargetKey(continuation.target.apiContractId, continuation.target.endpointId)
        )
      }
      setGeneratingDetailTargetKey('')
    } else {
      setEntityDesignReturning(true)
      clearActiveSession()
    }
    onPlanningArtifactsRefresh()
  }, [
    activeDetailTarget,
    activeSession,
    activeWorkflow,
    clearActiveSession,
    developmentPlanningApiContracts,
    displayedPlanningPages,
    loading,
    onPlanningArtifactsRefresh,
  ])

  // 大纲刷新把实体标记为已设计后，解除返回态抑制，避免误锁后续入口。
  useEffect(() => {
    if (
      entityDesignReturning &&
      Boolean(activeEntityOption?.designed || activeEntityOption?.hasDetailPlan)
    ) {
      setEntityDesignReturning(false)
    }
  }, [activeEntityOption, entityDesignReturning])

  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    if (activeApiEndpoint) return
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
  }, [activeApiEndpoint, displayedPlanningPages])

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

  /** 验收不通过只解锁当前验收会话的普通对话，不向后端提交验收结果。 */
  const handleAcceptanceReject = useCallback((): void => {
    setAcceptanceConversationSessionKey(activeSession?.key || draftKey)
  }, [activeSession?.key, draftKey])

  /** 验收通过暂未接线，保留按钮并明确告知用户当前能力边界。 */
  const handleAcceptanceApprove = useCallback((): void => {
    message.info('验收通过功能暂未开放')
  }, [])

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
    setTemporaryChatOpen(false)
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('skills')
  }

  const handleShowFiles = (): void => {
    setTemporaryChatOpen(false)
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('files')
  }

  const handleShowSettings = (): void => {
    setTemporaryChatOpen(false)
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('settings')
  }

  /** 打开独立数据源管理页，并退出当前对话目标上下文。 */
  const handleShowDataSources = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('dataSources')
  }

  /** 打开独立临时对话浮层，不改变底层工作区选择和会话上下文。 */
  const handleOpenTemporaryChat = (): void => {
    if (phaseSessionRunActive) return
    setTemporaryChatOpen(true)
  }

  /** 关闭临时对话浮层并完整保留底层工作区状态。 */
  const handleCloseTemporaryChat = useCallback((): void => {
    setTemporaryChatOpen(false)
  }, [])

  /** 从历史面板进入空白对话草稿，首轮发送前不创建历史记录。 */
  const handleCreateChatSession = (): void => {
    if (phaseSessionRunActive) return
    setTemporaryChatOpen(false)
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    handleCreateSessionFromList()
  }

  /** 启动当前页面的详细设计；页面状态后续由正式开发产物更新。 */
  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<boolean> => {
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
      setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
    }
    return started
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
  ): Promise<boolean> => {
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
    return started
  }

  /** 根据锁定入口里的目标类型启动页面、接口或实体详细设计。 */
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
      continuation?: WorkflowDevelopmentContinuation
    }
  ): Promise<void> => {
    if (pendingDagExecution) return
    if (targetType === 'endpoint') {
      await handleStartEndpointDesign(targetId, targetLabel, hasDetailPlan, targetContext)
      return
    }
    if (targetType === 'entity') {
      const targetKey = `entity:${targetId}`
      setInteractingDetailTargetKey(targetKey)
      setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
      setActiveDetailTarget({ type: 'entity', entityId: targetId, label: targetLabel })
      const started = await handleStartEntityDetailConfirmation({
        entityId: targetId,
        entityLabel: targetLabel,
        hasDetailPlan,
        continuation: targetContext?.continuation
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

  /** 从空白对话快捷任务创建通用历史会话，并仅为本次正式运行设置页面、Endpoint 或实体目标。 */
  const handleQuickTaskStart = async (task: QuickTaskItem): Promise<void> => {
    if (pendingDagExecution) return
    setTemporaryChatOpen(false)
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    if (task.kind === 'page') {
      await handleStartPageDesign(task.pageId, task.pageLabel, task.hasDetailPlan)
      return
    }
    if (task.kind === 'entity') {
      await handleStartDetailDesign(
        'entity',
        task.entityId,
        task.entityLabel,
        task.hasDetailPlan
      )
      return
    }
    await handleStartEndpointDesign(task.endpointId, task.endpointLabel, task.hasDetailPlan, {
      apiContractId: task.apiContractId,
      endpointId: task.endpointId
    })
  }

  /** 消费实体完成续接卡，在同一历史会话中重新启动原页面或 Endpoint 正式任务。 */
  const handleContinueDevelopment = async (
    messageId: number,
    continuation: ChatSessionDevelopmentContinuation
  ): Promise<void> => {
    if (!activeSession || continuation.status === 'started' || loading || workspaceBusy) return
    /** 只更新指定续接消息的消费状态，并保留当前会话中随后追加的运行消息。 */
    const updateContinuationStatus = (
      status: ChatSessionDevelopmentContinuation['status']
    ): AgentChatMessage[] =>
      getSessionMessages(activeSession.key).map((item) =>
        item.id === messageId && item.developmentContinuation
          ? {
              ...item,
              developmentContinuation: { ...item.developmentContinuation, status }
            }
          : item
      )
    /** 同步续接卡状态到内存与当前会话文件。 */
    const persistContinuationStatus = async (nextMessages: AgentChatMessage[]): Promise<void> => {
      setSessionMessages(activeSession.key, nextMessages)
      await persistSession({
        editorMode: activeSession.editorMode,
        messages: nextMessages,
        sessionId: activeSession.sessionId,
        threadId: activeSession.threadId
      })
    }

    const startedMessages = updateContinuationStatus('started')
    try {
      await persistContinuationStatus(startedMessages)
      const started = await continueDevelopmentExecution(continuation)
      const target = continuation.target
      if (started) {
        if (target.type === 'page') {
          setActiveDetailTarget({ type: 'page', pageId: target.pageId })
          setInteractingDetailTargetKey(pageDetailTargetKey(target.pageId))
        } else {
          setActiveDetailTarget({
            type: 'endpoint',
            apiContractId: target.apiContractId,
            endpointId: target.endpointId,
            endpointKey: `${target.apiContractId}:${target.endpointId}`,
            label: target.label
          })
          setInteractingDetailTargetKey(
            endpointDetailTargetKey(target.apiContractId, target.endpointId)
          )
        }
      }
      if (!started) await persistContinuationStatus(updateContinuationStatus('ready'))
    } catch (error) {
      await persistContinuationStatus(updateContinuationStatus('ready')).catch(() => undefined)
      message.error(formatError(error, '继续开发失败'))
    }
  }

  /** 从实体设计门禁卡片一键跳转到对应实体的设计流程。 */
  const handleEntityDesignGateJump = async (
    entityId: string,
    workflow: WorkflowRunPayload
  ): Promise<void> => {
    const entity = developmentPlanningEntities.find((item) => item.id === entityId)
    const continuation = workflowDevelopmentContinuation(
      workflow,
      displayedPlanningPages,
      developmentPlanningApiContracts
    )
    if (
      !continuation ||
      continuation.status !== 'awaiting_entity_binding' ||
      continuation.action !== 'start_entity_binding'
    ) {
      message.error('当前实体门禁缺少有效的开发续接合同，请重新发起页面或接口开发。')
      return
    }
    await handleStartDetailDesign(
      'entity',
      entityId,
      entity?.label || entityId,
      Boolean(entity?.hasDetailPlan),
      { continuation }
    )
  }

  /** 从历史面板恢复原有会话，并关闭临时对话浮层避免遮挡持久会话。 */
  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setTemporaryChatOpen(false)
    setActiveView('chat')
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setActiveDetailTarget({ type: 'none' })
    await handleOpenSession(sessionId)
  }

  /** 始终用原会话身份提交固定 DAG 卡；确认启动后才把中央对话切回该会话。 */
  const handlePinnedDagConfirmation = async (
    action: WorkflowBuildTaskPlanConfirmation
  ): Promise<void> => {
    if (!pendingDagWorkflow || !pendingDagSession) {
      setDagConfirmationSubmissionError('原会话仍在恢复中，请稍后重试。')
      return
    }
    try {
      setDagConfirmationSubmissionError('')
      const identity = await loadSessionIdentity(pendingDagSession.id)
      const submitted = await handleSubmitClarification(
        pendingDagWorkflow,
        { build_task_plan_confirmation: action },
        {
          sessionIdentity: identity,
          onExecutionStarted:
            action.action === 'confirm'
              ? () => {
                  void handleOpenChatSession(pendingDagSession.id)
                }
              : undefined
        }
      )
      if (!submitted) {
        setDagConfirmationSubmissionError(
          action.action === 'confirm'
            ? '确认提交失败，任务计划仍保持待确认；请重试或放弃流程。'
            : '放弃提交失败，流程仍保持锁定；请重试。'
        )
      }
    } catch (error) {
      setDagConfirmationSubmissionError(
        formatError(
          error,
          action.action === 'confirm'
            ? '确认提交失败，任务计划仍保持待确认'
            : '放弃提交失败，流程仍保持锁定'
        )
      )
    }
  }

  /** 从阶段交接回执打开对应会话，不改变后端权威 Graph 身份。 */
  const handleOpenRevisionSession = async (
    handoff: NonNullable<AgentChatMessage['revisionHandoff']>
  ): Promise<void> => {
    try {
      setActiveView('chat')
      setActiveDetailTarget({ type: 'none' })
      const phase =
        handoff.kind === 'revision_development'
          ? 'development'
          : handoff.kind === 'revision_planning'
            ? 'planning'
            : initialFormalRevisionPhase(handoff.formalBranch)
      // 先验证并登记目标阶段会话，再切换阶段；否则阶段恢复 effect 可能用旧选择覆盖显式目标。
      await openSessionForPhase(handoff, phase)
      switchPhase(phase)
    } catch (error) {
      message.error(formatError(error, '打开正式修改会话失败'))
    }
  }

  /** 提交详细设计确认后进入 DAG/构建链路，停止使用详细设计生成进度遮罩。 */
  const handleSubmitWorkflowClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>
  ): Promise<void> => {
    setGeneratingDetailTargetKey('')
    // 设计阶段：规划确认走 planningSubmitRef（Modal 的 runPlanning），不走开发 workflow。
    if (isApplicationPlanningPhase) {
      // 空答案 = UI 设计稿生成池轮询（no-op resume）：不开启新一轮、不追加用户消息，
      // 也不走 ensureApplicationPlanningAction（空 answers 会被误判为 confirm）。
      // 直接把空 answers 传给 onSubmitPlanningClarification，由 Modal 拦截走恢复路径。
      const isUiDesignPoll = !answers || Object.keys(answers).length === 0
      if (isUiDesignPoll) {
        void onSubmitPlanningClarification(workflow, {}, editedRequirementSpec).catch(
          () => undefined
        )
        return
      }
      const planningAnswers = ensureApplicationPlanningAction(workflow, answers)
      const revisionDraftAction =
        planningAnswers && typeof planningAnswers.revision_draft_interaction === 'object'
          ? String(
              (planningAnswers.revision_draft_interaction as { action?: unknown }).action || ''
            )
          : ''
      const revisionTechnicalPlanConfirmed =
        Boolean(activeFormalRevision) &&
        (revisionDraftAction === 'confirm' ||
          (planningWorkflowPhase(workflow) === 'technical_planning' &&
            planningAnswers.__applicationPlanningAction === 'confirm'))
      // UI 设计稿的单页动作（换一换/选模板/调整）是同一轮内的更新，不新增消息卡片，
      // 只更新现有卡片；跳过与确认全部等推进到下一阶段的操作才新增卡片并留痕。
      const isUiDesignPageAction =
        planningAnswers && 'ui_design_action' in planningAnswers
          ? (planningAnswers as { ui_design_action?: { action?: string } }).ui_design_action
              ?.action !== 'skip'
          : false
      if (!isUiDesignPageAction) {
        planningNewRoundRef.current = true
        // 离开 UI 确认阶段（确认全部/跳过/进入规划）：清空 UI 确认阶段标记，
        // 避免后续阶段的纯 content 被误当作 UI 轮询 content 丢弃。
        lastUiDesignRunIdRef.current = undefined
        if (planningAnswers.__applicationPlanningAction === 'enter_planning') {
          const checkpointThreadId = String(planningThreadId || workflow.threadId || '').trim()
          if (!checkpointThreadId) {
            message.error('当前规划 checkpoint 标识缺失，请重新打开应用后重试。')
            return
          }
          // 每次从设计阶段进入规划阶段都创建新的前端会话；后端仍复用原 planning
          // checkpoint thread，避免二次修改沿用旧设计会话导致入口提交没有新会话承载。
          // 首次创建沿用当前 DESIGN 会话作为交接来源；formal revision 必须按 lifecycle
          // 完整身份从所有会话中重新解析，不能信任可能被阶段恢复抢占的 activeSession。
          const formalRevisionSourceSession = activeFormalRevision
            ? formalRevisionPlanningSourceSession(allSessions, applicationLifecycle, application.id)
            : undefined
          const sourceIdentity = activeFormalRevision
            ? formalRevisionSourceSession
              ? await loadSessionIdentity(formalRevisionSourceSession.id)
              : undefined
            : activeSession
          const revisionContext = bindRevisionSessionChangeId(
            sourceIdentity?.revisionContext,
            applicationLifecycle
          )
          if (
            activeFormalRevision &&
            (!sourceIdentity ||
              !revisionContext ||
              revisionContext.sessionRole !== 'design' ||
              revisionContext.changeId !== activeFormalRevision.changeId ||
              revisionContext.formalBranch !== activeFormalRevision.formalBranch)
          ) {
            planningNewRoundRef.current = false
            message.error('找不到与本次二次修改匹配的需求设计会话，已停止进入规划阶段。')
            return
          }
          const planningInterrupt = [workflow.result, workflow.state]
            .map((value) => value?.application_planning_interrupt)
            .find((value) => value && typeof value === 'object' && !Array.isArray(value)) as
            | Record<string, unknown>
            | undefined
          const gateIdentity =
            String(planningInterrupt?.gateId || '').trim() ||
            String(planningInterrupt?.artifactRevision || '').trim() ||
            workflow.runId
          const stageEntryKey = planningStageTransitionKey(
            checkpointThreadId,
            gateIdentity,
            revisionContext
          )
          if (planningStageTransitionRef.current === stageEntryKey) return
          planningStageTransitionRef.current = stageEntryKey
          let planningIdentity: SessionIdentity | undefined
          let sourceMessages: AgentChatMessage[] = []
          let sourceMessagesWithReceipt: AgentChatMessage[] = []
          let sourceReceiptAdded = false
          let sourceReceiptPersisted = false
          try {
            // 首次创建和二次修改都先新建独立 PLAN StageSession/conversation thread，
            // 再切阶段并恢复原 Graph checkpoint，两个 thread 身份不得混用。
            planningIdentity = await ensurePlanningSession(
              stageEntryKey,
              'planning',
              revisionContext,
              sourceIdentity
            )
            if (revisionContext?.changeId && sourceIdentity) {
              sourceMessages = getSessionMessages(sourceIdentity.key)
              const receiptExists = sourceMessages.some(
                (item) =>
                  item.revisionHandoff?.kind === 'revision_planning' &&
                  item.revisionHandoff.changeId === revisionContext.changeId &&
                  item.revisionHandoff.targetSessionId === planningIdentity?.sessionId
              )
              if (!receiptExists) {
                sourceReceiptAdded = true
                const receiptId = Date.now() * 1000
                const nextSourceMessages: AgentChatMessage[] = [
                  ...sourceMessages,
                  {
                    id: receiptId,
                    role: 'assistant',
                    content: '',
                    revisionHandoff: {
                      kind: 'revision_planning',
                      formalBranch: revisionContext.formalBranch,
                      targetSessionId: planningIdentity.sessionId,
                      targetConversationThreadId: planningIdentity.threadId,
                      impactInteractionId: revisionContext.impactInteractionId,
                      changeId: revisionContext.changeId,
                      request: String(
                        activeFormalRevision?.request ||
                          '需求设计已确认，进入本次二次修改的技术规划阶段。'
                      )
                    },
                    createdAt: receiptId
                  }
                ]
                sourceMessagesWithReceipt = nextSourceMessages
                setSessionMessages(sourceIdentity.key, nextSourceMessages)
                await persistSession({
                  editorMode: sourceIdentity.editorMode,
                  messages: nextSourceMessages,
                  sessionId: sourceIdentity.sessionId,
                  threadId: sourceIdentity.threadId,
                  revisionContext
                })
                sourceReceiptPersisted = true
              }
            }
            planningSessionKeyRef.current = planningIdentity.key
            setLocalPlanningConversationThreadId(planningIdentity.threadId)
            appendPlanningUserMessage(planningAnswers)
            switchPhase('planning')
            await onSubmitPlanningClarification(workflow, planningAnswers, editedRequirementSpec)
          } catch (error) {
            if (planningStageTransitionRef.current === stageEntryKey) {
              planningStageTransitionRef.current = ''
            }
            let sourceReceiptRolledBack = true
            if (sourceIdentity && sourceReceiptAdded) {
              setSessionMessages(sourceIdentity.key, sourceMessages)
              if (sourceReceiptPersisted) {
                try {
                  await persistSession({
                    editorMode: sourceIdentity.editorMode,
                    messages: sourceMessages,
                    sessionId: sourceIdentity.sessionId,
                    threadId: sourceIdentity.threadId,
                    revisionContext
                  })
                } catch (rollbackError) {
                  sourceReceiptRolledBack = false
                  // 磁盘仍保留回执时，内存也恢复为同一状态，并保留目标会话维持可跳转关系。
                  setSessionMessages(sourceIdentity.key, sourceMessagesWithReceipt)
                  message.warning(formatError(rollbackError, '规划阶段交接回执回滚失败'))
                }
              }
            }
            if (planningIdentity && sourceReceiptRolledBack) {
              try {
                await discardPreparedSession(planningIdentity)
              } catch (rollbackError) {
                message.warning(formatError(rollbackError, '预创建规划会话清理失败'))
              }
            }
            setLocalPlanningConversationThreadId('')
            switchPhase('product')
            message.error(formatError(error, '进入规划阶段失败'))
          }
          return
        } else {
          // 其它确认/放弃/填表操作继续保留用户消息与即时加载占位。
          // TechnicalPlan 二次修改确认后继续停留在当前规划会话；只有服务端
          // continuation 被开发 Workflow 成功接管后，才激活 DEVELOPMENT StageSession。
          suppressRevisionTechnicalPlanTransitionRef.current = revisionTechnicalPlanConfirmed
          appendPlanningUserMessage(planningAnswers, !revisionTechnicalPlanConfirmed)
        }
      }
      void onSubmitPlanningClarification(workflow, planningAnswers, editedRequirementSpec).catch(
        () => {
          if (revisionTechnicalPlanConfirmed) {
            suppressRevisionTechnicalPlanTransitionRef.current = false
          }
        }
      )
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
    // 设计阶段二次修改同样不能沿用开发阶段页面的临时生成状态。
    setGeneratingDetailTargetKey('')
    planningNewRoundRef.current = true
    lastUiDesignRunIdRef.current = undefined
    appendPlanningUserMessage({ design_change_request: trimmed })
    void onSubmitPlanningClarification(planningWorkflow, {}, undefined, undefined, trimmed).catch(
      () => undefined
    )
    setDraftByKey(draftKey, '')
  }

  /** 滚动到现有 Workflow 进度区域，不改变消息列表和中央内容结构。 */
  const handleViewPlan = (): void => {
    document.querySelector(`.${CLASS_PREFIX}-process-steps`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center'
    })
  }

  /** 用户点击"进入开发阶段"：放开 planning 锁并进入带快捷任务的空白对话。 */
  const handleEnterDevelopment = useCallback((): void => {
    markApplicationEnteredDevelopment(application.id)
    setEnterDevConfirmed(true)
    clearActiveSession()
    setActiveDetailTarget({ type: 'none' })
    setInteractingDetailTargetKey('')
    setGeneratingDetailTargetKey('')
    setRightPanel(undefined)
    setActiveView('chat')
    switchPhase(null)
  }, [application.id, switchPhase, clearActiveSession, setRightPanel])

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
        acceptanceAwaiting && 'acceptance-awaiting',
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
          forceCollapsed
          loadingSessions={loadingSessions}
          temporaryChatActive={temporaryChatOpen}
          outlineLocked={false}
          onCloseTemporaryChat={handleCloseTemporaryChat}
          onCreateFreeChatSession={handleCreateChatSession}
          onDeleteSession={handleDeleteSession}
          onOpenTemporaryChat={handleOpenTemporaryChat}
          onOpenSession={handleOpenChatSession}
          onReturnWelcome={onReturnWelcome}
          onShowFiles={handleShowFiles}
          onShowDataSources={handleShowDataSources}
          onShowSettings={handleShowSettings}
          onShowSkills={handleShowSkills}
          onThemeChange={onThemeChange}
          pages={displayedPlanningPages}
          pageTree={displayedPlanningPageTree}
          apiContracts={developmentPlanningApiContracts}
          entities={developmentPlanningEntities}
          {...artifactOutlineProps}
          filesActive={activeView === 'files'}
          dataSourcesActive={activeView === 'dataSources'}
          dataSourcesEnabled={!isApplicationPlanningPhase && Boolean(application.workspaceRoot)}
          sessionError={sessionError}
          sessionCreationDisabled={phaseSessionRunActive}
          sessionRunStates={displayedSessionRunStates}
          sessions={sessions}
          settingsActive={activeView === 'settings'}
          skillsActive={activeView === 'skills'}
          theme={theme}
          workspaceRoot={workspaceRoot}
        />
        {activeView === 'skills' ? (
          <SkillsPage onSkillDisabled={handleSkillDisabled} theme={theme} />
        ) : activeView === 'dataSources' ? (
          <DataSourcesPage theme={theme} workspaceRoot={application.workspaceRoot || ''} />
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
              activeDagStageId={activeDagStageId}
              applicationLifecycle={applicationLifecycle}
              applicationTemplatePreparationEligible={applicationTemplatePreparationEligible}
              codeChangeActionsDisabled={
                loading || workflowInputLocked || otherSessionExecutionLocked
              }
              conversationRunning={conversationRunning}
              dagConfirmationInStageOutput={Boolean(
                stageOutputMatchesSession &&
                  rightPanel?.type === 'stage-output' &&
                  rightPanel.view === 'confirmation' &&
                  dagConfirmationPlan
              )}
              entityDesignSession={entityDesignChatActive}
              designPhasePlanning={isApplicationPlanningPhase}
              emptyContent={
                !isApplicationPlanningPhase ? (
                  <QuickTaskGuide
                    apiContracts={developmentPlanningApiContracts}
                    disabled={loading || workflowInputLocked}
                    entities={developmentPlanningEntities}
                    loading={loadingSessions || !developmentPlanningReady}
                    onStart={handleQuickTaskStart}
                    pages={displayedPlanningPages}
                  />
                ) : undefined
              }
              error={planningError || error}
              key={activeSession?.key || draftKey}
              loading={loading || otherSessionExecutionLocked}
              messages={messages}
              onContinueDevelopment={handleContinueDevelopment}
              onEntityDesignGateJump={handleEntityDesignGateJump}
              onDagStageSelect={handleDagStageSelect}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onOpenRevisionSession={handleOpenRevisionSession}
              onRevertCodeChanges={requestCodeChangeRevert}
              onRetryError={
                planningError
                  ? onRetryPlanning
                  : workflowCodeReviewRetry(activeWorkflow)
                    ? () => void handleRetryCodeReview()
                    : undefined
              }
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
            />

            {otherSessionExecutionLocked || pendingDagExecution ? (
              <SessionExecutionLockDock
                phaseLabel={WORKBENCH_PHASE_AGENTS[activeWorkbenchPhase].label}
                sessionTitle={phaseExecutionSessionTitle}
                status={phaseExecutionStatus}
              />
            ) : !entityDesignChatActive &&
            !acceptanceAwaiting &&
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
                workspaceBusy={workflowInputLocked}
                workspaceRoot={workspaceRoot}
              />
            ) : designChangeInputLocked ? (
              <DesignChangeLockDock
                disabled={loading || workflowInputLocked}
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
                  inspectedElementContext={inspectedElementContext}
                  loading={loading}
                  onDraftChange={(value) => setDraftByKey(draftKey, value)}
                  onInspectedElementContextClear={() => setInspectedElementContext(undefined)}
                  onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                  // 设计阶段仍可修订时，专用输入先做设计意图识别；模板就绪后恢复普通 Coordinator 对话。
                  // 当前节点的澄清和确认只能通过上方结构化卡片提交，不能劫持普通输入语义。
                  onSend={
                    designChangeWorkflowAvailable ? handleDesignChangeSend : handleConversationSend
                  }
                  onStopGenerating={handleStopGenerating}
                  stopping={stopping}
                  selectedSkills={selectedSkills}
                  workspaceBusy={workflowInputLocked}
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
                    workspaceBusy={workflowInputLocked}
                    workspaceRoot={workspaceRoot}
                  />
                ) : null}
              </>
            )}

            {/* 未完成的页面、实体绑定与 endpoint 设计都先显示统一锁定蒙层。 */}
            {((requiresEntitySourceBinding(activeEntityOption) &&
              entityDesignChatActive &&
              !entitySessionActive &&
              !entityDesignReturning) ||
              showEndpointDetailDesignEntry ||
              showPageDetailDesignEntry) &&
            displayedPlanExecutionMode === 'idle' &&
            !detailConfirmationWaitingReview ? (
              <DetailConfirmationPageSelector
                disabled={loading || workflowInputLocked}
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
      </div>

      {temporaryChatOpen ? <TemporaryChatOverlay onClose={handleCloseTemporaryChat} /> : null}

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

      {showRightPanel && rightPanel?.type === 'outline' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            <DevelopmentArtifactsPanel
              apiContracts={developmentPlanningApiContracts}
              entities={developmentPlanningEntities}
              detailLabel={artifactDetailLabel}
              outlineLocked={false}
              pages={displayedPlanningPages}
              pageTree={displayedPlanningPageTree}
              {...artifactOutlineProps}
            />
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'doc' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
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
            ) : isApplicationPlanningPhase ? (
              <DocPanel
                content={designDocContent}
                docName={designDocName}
                generating={designDocGenerating}
                productPlan={
                  requirementDocViewActive ? requirementProductPlanForDoc : productPlanForDoc
                }
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
                generating={designDocGenerating}
                title={designDocTitle}
              />
            )}
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'review-report' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            <DocPanel
              content={reviewReportContent}
              docName="审查报告"
              error={reviewReportError}
              generating={reviewReportLoading}
              title={reviewReportPath}
            />
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'test-report' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            <DocPanel
              content={testReportContent}
              docName="测试报告"
              error={testReportError}
              generating={testReportLoading}
              title={testReportPath}
            />
          </div>
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'preview' && (
        <div className={cx('embedded-preview-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
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
            onElementContextChange={setInspectedElementContext}
          />
          {acceptanceAwaiting && (
            <AcceptanceDecisionDock
              disabled={loading || workspaceBusy}
              onAccept={handleAcceptanceApprove}
              onReject={handleAcceptanceReject}
            />
          )}
        </div>
      )}

      {showRightPanel && rightPanel?.type === 'source' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
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

      {showRightPanel && rightPanel?.type === 'stage-output' && (
        <div className={cx('embedded-preview-pane', 'workspace-pane')}>
          <RightPanelTabs
            tabs={displayedWorkspaceTabs}
            active={displayedWorkspaceTab}
            onChange={openDisplayedWorkspaceTab}
            onClose={() => {
              setRightPanel(undefined)
              onRightPanelOpenChange(false)
            }}
          />
          <div className={cx('workspace-content')}>
            {stageOutputMatchesSession ? (
              rightPanel.view === 'confirmation' && dagConfirmationPlan ? (
                <StageOutputPanel
                  confirmationDisabled={
                    loading ||
                    workspaceBusy ||
                    !stageOutputWorkflow ||
                    workflowInteractionAvailability(stageOutputWorkflow, applicationLifecycle) !==
                      'active'
                  }
                  confirmationErrors={dagConfirmationErrors}
                  confirmationPlan={dagConfirmationPlan}
                  confirmationTargetReview={dagConfirmationTargetReview}
                  onConfirmationSubmit={(action: WorkflowBuildTaskPlanConfirmation) => {
                    void handlePinnedDagConfirmation(action)
                  }}
                />
              ) : rightPanel.view === 'stage' && selectedDagStage?.output ? (
                <StageOutputPanel
                  onReturnToConfirmation={
                    currentStageOutputPhase === 'confirmation' && dagConfirmationPlan
                      ? handleReturnDagConfirmation
                      : undefined
                  }
                  stage={selectedDagStage}
                />
              ) : pendingDagExecution && rightPanel.view === 'confirmation' ? (
                <Alert
                  message="正在恢复待确认的任务计划"
                  description="确认卡会固定显示在这里，恢复完成前不会释放当前流程锁。"
                  showIcon
                  type="info"
                />
              ) : null
            ) : null}
          </div>
        </div>
      )}

      {showRightPanel && codeDiffVisible && rightPanel?.type === 'diff' && (
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

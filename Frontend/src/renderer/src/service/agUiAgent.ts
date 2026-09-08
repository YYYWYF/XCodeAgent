import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber, HttpAgent } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'
import type {
  ApplicationConfig,
  ApplicationPlanningInteraction,
  ApplicationLifecycle,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowConfirmationArtifact,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowAction,
  WorkflowEvent,
  WorkflowProjectPlanUpdate,
  WorkflowRevisionDraftInteraction,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet,
  InspectedElementContext
} from '../typings'

export type SendWorkflowMessageOptions = {
  workspaceRoot?: string
  editorMode: EditorMode
  application?: ApplicationConfig
  clarificationAnswers?: WorkflowClarificationAnswers
  applicationPlanningInteraction?: ApplicationPlanningInteraction
  editedRequirementSpec?: Record<string, unknown>
  requirementSpecFeedback?: string
  applicationPlanningRecovery?: {
    action: 'get'
    workspaceRoot: string
    applicationId?: string
  }
  originalRequest?: string
  selectedSkillNames?: string[]
  selectedPageId?: string
  selectedApiContractId?: string
  selectedEndpointId?: string
  selectedEntityId?: string
  detailTargetType?: 'page' | 'endpoint' | 'entity'
  buildExecutionScope?: WorkflowBuildExecutionScope
  workflowAction?: WorkflowAction
  revisionRequest?: Record<string, unknown>
  revisionContinuation?: {
    changeId: string
    token: string
  }
  developmentContinuation?: {
    id: string
    token?: string
  }
  revisionInteraction?: WorkflowRevisionDraftInteraction
  workflowDebug?: WorkflowDebugOptions
  resumeState?: WorkflowRunPayload
  workflowScope?: string
  onContent?: (content: string) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onToolCalls?: (toolCalls: ToolCallRecord[]) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
  planControlAction?: 'stop' | 'end'
  planControlRunId?: string
  resumeExecutionRunId?: string
  pageTemplate?: {
    id?: string
    name?: string
    sourcePath?: string
  }
  conversation?: boolean
  conversationTarget?:
    | {
        type: 'page'
        pageId: string
      }
    | {
        type: 'endpoint'
        apiContractId: string
        endpointId: string
      }
  conversationElementContext?: InspectedElementContext
  conversationApprovedPaths?: string[]
  conversationHandoffDecision?: 'approved' | 'rejected'
  conversationImpactInteractionId?: string
}

/** 将已选 DOM 的源码文件和行号直接附加到普通二次修改需求。 */
export function appendElementContextToConversationPrompt(
  message: string,
  context?: InspectedElementContext
): string {
  if (!context) return message
  const sourcePath = context.sourcePath.replace(/^\/+/, '')
  const frontendPath = `frontend/${sourcePath}`
  return `${message}\n\n代码实现位于${frontendPath}文件 的${context.line}行附近处，请参考${frontendPath}文件 的${context.line}行附近处的相关代码实现进行修改。`
}

/** 构建 `/workflow/run` 的 AG-UI forwardedProps，集中维护技能、控制和恢复字段。 */
export function buildWorkflowForwardedProps(
  options: SendWorkflowMessageOptions
): Record<string, unknown> {
  return {
    workspaceRoot: options.workspaceRoot,
    editorMode: options.editorMode,
    application: options.application,
    clarificationAnswers: options.clarificationAnswers,
    applicationPlanningInteraction: options.applicationPlanningInteraction,
    editedRequirementSpec: options.editedRequirementSpec,
    requirementSpecFeedback: options.requirementSpecFeedback,
    applicationPlanningRecovery: options.applicationPlanningRecovery,
    originalRequest: options.originalRequest,
    selectedSkillNames: options.selectedSkillNames,
    selectedPageId: options.selectedPageId,
    selectedApiContractId: options.selectedApiContractId,
    selectedEndpointId: options.selectedEndpointId,
    selectedEntityId: options.selectedEntityId,
    detailTargetType: options.detailTargetType,
    workflowAction: options.workflowAction,
    revisionRequest: options.revisionRequest,
    revisionContinuation: options.revisionContinuation,
    developmentContinuation: options.developmentContinuation,
    revisionInteraction: options.revisionInteraction,
    workflowDebug: options.workflowDebug,
    resumeFrom: options.workflowDebug?.enabled ? options.workflowDebug.resumeFrom : undefined,
    buildExecutionScope:
      options.buildExecutionScope ||
      (options.workflowDebug?.enabled ? options.workflowDebug.buildExecutionScope : undefined),
    resumeState: options.resumeState,
    workflowScope: options.workflowScope,
    planControlAction: options.planControlAction,
    planControlRunId: options.planControlRunId,
    resumeExecutionRunId: options.resumeExecutionRunId,
    pageTemplate: options.pageTemplate,
    conversation: options.conversation
      ? {
          workspaceRoot: options.workspaceRoot,
          selectedSkillNames: options.selectedSkillNames,
          ...(options.conversationTarget !== undefined
            ? { target: options.conversationTarget }
            : {}),
          ...(options.conversationElementContext !== undefined
            ? { elementContext: options.conversationElementContext }
            : {}),
          ...(options.originalRequest !== undefined
            ? { originalRequest: options.originalRequest }
            : {}),
          ...(options.conversationApprovedPaths !== undefined
            ? { approvedPaths: options.conversationApprovedPaths }
            : {}),
          ...(options.conversationHandoffDecision !== undefined
            ? { handoffDecision: options.conversationHandoffDecision }
            : {}),
          ...(options.conversationImpactInteractionId !== undefined
            ? { impactInteractionId: options.conversationImpactInteractionId }
            : {})
        }
      : undefined
  }
}

export type AgUiChatResult = {
  threadId: string
  runId: string
  answer: string
  workflow?: WorkflowRunPayload
  toolCalls: ToolCallRecord[]
  processSteps: ProcessStepRecord[]
  assistantMessage?: Message
}

export class AgUiRunError extends Error {
  readonly code?: string
  readonly workflow?: WorkflowRunPayload
  readonly toolCalls: ToolCallRecord[]
  readonly processSteps: ProcessStepRecord[]

  /** 保存 AG-UI RUN_ERROR 携带的最终 Workflow 快照，供消息层持久化失败结果。 */
  constructor(
    message: string,
    options: {
      code?: string
      workflow?: WorkflowRunPayload
      toolCalls?: ToolCallRecord[]
      processSteps?: ProcessStepRecord[]
    } = {}
  ) {
    super(message)
    this.name = 'AgUiRunError'
    this.code = options.code
    this.workflow = options.workflow
    this.toolCalls = options.toolCalls || []
    this.processSteps = options.processSteps || []
  }
}

export type ToolCallRecord = {
  id: string
  name: string
  args: string
  result?: string
  status: 'running' | 'completed'
}

export type IntegrationTestCheckRecord = {
  id: string
  name: string
  status: 'running' | 'passed' | 'skipped' | 'failed'
  required: boolean
  advisory?: boolean
  evidence?: string
  passedTests?: number
  totalTests?: number
  performanceScores?: {
    performance?: number
    accessibility?: number
    bestPractices?: number
    seo?: number
  }
  performanceMetrics?: {
    fcp?: number
    lcp?: number
    tbt?: number
    cls?: number
    si?: number
  }
  reportPath?: string
}

export type DagGenerationRunStatus = 'active' | 'failed' | 'cancelled'

export type DagGenerationPhase =
  | 'preparing'
  | 'generating_units'
  | 'global_check'
  | 'assembling'
  | 'validating'
  | 'persisting_pending'

export type DagGenerationParticipation =
  | 'reuse_only'
  | 'generate_only'
  | 'reuse_and_generate'
  | 'prerequisite_only'
  | 'structural_only'

export type DagGenerationStrategy =
  | 'structural_only'
  | 'prerequisite_only'
  | 'reuse_only'
  | 'deterministic'
  | 'model'

export type DagGenerationUnitStatus =
  | 'not_required'
  | 'pending'
  | 'generating'
  | 'validating'
  | 'candidate_ready'
  | 'round_exhausted'
  | 'aborted'

export type DagGenerationIssueRecord = {
  code: string
  level: string
  category: string
  unitIds: string[]
  retryUnitIds: string[]
  retryable: boolean
  message: string
}

export type DagGenerationUnitRecord = {
  id: string
  kind: string
  participation: DagGenerationParticipation
  generationStrategy: DagGenerationStrategy
  status: DagGenerationUnitStatus
  generationRound: number
  attemptInRound: number
  localAttemptLimit: number
  totalAttempts: number
  retainedTaskCount: number
  reusableCapabilityCount: number
  candidateTaskCount: number
  issues: DagGenerationIssueRecord[]
}

export type DagGenerationSummary = {
  unitCount: number
  readyUnitCount: number
  pendingUnitCount: number
  activeUnitCount: number
  roundExhaustedUnitCount: number
  abortedUnitCount: number
  retainedTaskCount: number
  candidateTaskCount: number
  unitIssueCount: number
  globalIssueCount: number
  failureIssueCount: number
}

export type DagGenerationSnapshot = {
  schemaVersion: 'dag-generation.v1'
  planningRunId: string
  revision: number
  status: DagGenerationRunStatus
  phase: DagGenerationPhase
  globalRepairRound: number
  globalRepairLimit: number
  units: DagGenerationUnitRecord[]
  globalIssues: DagGenerationIssueRecord[]
  summary: DagGenerationSummary
}

const DAG_GENERATION_RUN_STATUSES = new Set<string>(['active', 'failed', 'cancelled'])
const DAG_GENERATION_PHASES = new Set<string>([
  'preparing',
  'generating_units',
  'global_check',
  'assembling',
  'validating',
  'persisting_pending'
])
const DAG_GENERATION_PARTICIPATIONS = new Set<string>([
  'reuse_only',
  'generate_only',
  'reuse_and_generate',
  'prerequisite_only',
  'structural_only'
])
const DAG_GENERATION_STRATEGIES = new Set<string>([
  'structural_only',
  'prerequisite_only',
  'reuse_only',
  'deterministic',
  'model'
])
const DAG_GENERATION_UNIT_STATUSES = new Set<string>([
  'not_required',
  'pending',
  'generating',
  'validating',
  'candidate_ready',
  'round_exhausted',
  'aborted'
])

export type WorkspaceInspectionPathItem = {
  path: string
  kind: string
}

export type CodeGraphDistribution = {
  kind: string
  count: number
}

export type CodeGraphSymbolPreview = {
  name: string
  kind: string
  language: string
  path: string
  lineStart: number
  lineEnd: number
}

export type WorkspaceInspectionSnapshot = {
  schemaVersion: string
  revision: string
  cacheHit: boolean
  fileManifest: {
    totalFiles: number
    sourceFiles: number
    truncated: boolean
  }
  techStack: string[]
  projectRoots: WorkspaceInspectionPathItem[]
  entrypoints: WorkspaceInspectionPathItem[]
  codeGraph: {
    provider: string
    providerVersion?: string
    status?: string
    available: boolean
    buildType?: string
    filesIndexed?: number
    symbolsIndexed?: number
    relationsIndexed?: number
    languages?: string[]
    nodesByKind?: CodeGraphDistribution[]
    relationsByKind?: CodeGraphDistribution[]
    sampleSymbols?: CodeGraphSymbolPreview[]
    warningCount?: number
    warnings?: string[]
    message?: string
    durationMs?: number
    cacheHit?: boolean
  }
}

export type WorkspaceInspectionProgress = {
  stage: string
  status: string
  message: string
  filesDiscovered: number
  filesIndexed: number
  symbolsIndexed: number
  relationsIndexed: number
  cacheHit: boolean
}

export type ProcessStepRecord = {
  id: string
  kind: 'reasoning' | 'tool' | 'command' | 'workflow'
  status: 'running' | 'completed' | 'failed' | 'requires_user_input'
  title: string
  detail: string
  result?: string
  sequence: number
  appendDetail?: boolean
  checks?: IntegrationTestCheckRecord[]
  nodeName?: string
  attempt?: number
  iterationKind?: string
  buildExecutionSlice?: import('../typings').WorkflowBuildExecutionSlice
  dagGeneration?: DagGenerationSnapshot
  workspaceInspection?: WorkspaceInspectionSnapshot
  workspaceInspectionProgress?: WorkspaceInspectionProgress
  projectPlanUpdate?: WorkflowProjectPlanUpdate
}

const DEFAULT_AGENT_BASE_URL = 'http://127.0.0.1:8000'

/** 解析桌面端注入的 Backend 地址，并为独立开发页面提供本地默认地址。 */
function getAgentBaseUrl(): string {
  return (window.xcodeAgent?.agentBaseUrl || DEFAULT_AGENT_BASE_URL).replace(/\/$/, '')
}

/** 返回主工作流的 AG-UI 地址。 */
export function getWorkflowUrl(): string {
  return `${getAgentBaseUrl()}/workflow/run`
}

/** 返回自由对话 Graph 的 AG-UI 地址。 */
export function getConversationUrl(): string {
  return `${getAgentBaseUrl()}/conversation/run`
}

export class AgUiChatSession {
  readonly threadId: string

  readonly endpointUrl: string

  private activeAgent?: HttpAgent
  private activeRunId?: string
  private activeRunCompletion?: Promise<void>
  private resolveActiveRunCompletion?: () => void

  /** 创建可指向主 Workflow 或同协议独立 Graph 的 AG-UI 会话。 */
  constructor(threadId = randomUUID(), url = getWorkflowUrl()) {
    this.threadId = threadId
    this.endpointUrl = url
  }

  /** 请求后端取消当前运行；确认失败时才本地中止，并等待取消请求完成。 */
  async stop(): Promise<void> {
    const runId = this.activeRunId
    const activeAgent = this.activeAgent
    const activeRunCompletion = this.activeRunCompletion
    if (!runId) {
      activeAgent?.abortRun()
      return
    }
    const cancelled = await this.cancelRun(runId)
    if (!cancelled && this.activeRunId === runId) activeAgent?.abortRun()
    await activeRunCompletion
  }

  /** 使用请求级 HttpAgent 发送当前消息，避免把本地会话历史和旧状态重复传输。 */
  async sendMessage(message: string, options: SendWorkflowMessageOptions): Promise<AgUiChatResult> {
    const requestAgent = createAgUiHttpAgent({
      url: this.endpointUrl,
      threadId: this.threadId
    })
    const userMessageId = randomUUID()
    requestAgent.addMessage({
      id: userMessageId,
      role: 'user',
      content: message
    })

    let workflow: WorkflowRunPayload | undefined
    let toolCalls: ToolCallRecord[] = []
    let processSteps: ProcessStepRecord[] = []
    let runErrorMessage = ''
    let runErrorCode: string | undefined
    const emitToolCalls = (nextToolCalls: ToolCallRecord[]): void => {
      toolCalls = nextToolCalls
      options.onToolCalls?.(toolCalls)
    }
    const subscriber: AgentSubscriber = {
      onCustomEvent: ({ event }) => {
        if (event.name === 'application-lifecycle') {
          const lifecycle = readApplicationLifecycle(event.value)
          if (lifecycle) options.onApplicationLifecycle?.(lifecycle)
        }
        if (event.name === 'agent-process') {
          const step = readProcessStep(event.value)
          if (step) {
            processSteps = mergeProcessStep(processSteps, step)
            options.onProcessSteps?.(processSteps)
          }
        }
        if (event.name === 'workflow-run') {
          workflow = readWorkflowPayload(event.value) ?? workflow
          if (workflow) {
            emitWorkflowLifecycle(workflow, options.onApplicationLifecycle)
            options.onWorkflow?.(workflow)
          }
        }
        if (event.name === 'conversation') {
          workflow = readWorkflowPayload(event.value) ?? workflow
          const step = readProcessStep(objectValue(event.value).processStep)
          if (step) {
            processSteps = mergeProcessStep(processSteps, step)
            options.onProcessSteps?.(processSteps)
          }
          if (workflow) options.onWorkflow?.(workflow)
        }
        if (event.name === 'llm.token') {
          // 规划模型 token 是内部 JSON 生成过程，只由 Workflow 事件驱动进度 UI，禁止写入聊天正文。
          const node = (event.value as { node?: string } | null)?.node || ''
          if (['product_planning', 'project_planning', 'technical_planning'].includes(node)) {
            return
          }
        }
      },
      onStateSnapshotEvent: ({ event }) => {
        workflow = readWorkflowFromState(event.snapshot) ?? workflow
        if (workflow) {
          emitWorkflowLifecycle(workflow, options.onApplicationLifecycle)
          options.onWorkflow?.(workflow)
        }
      },
      onTextMessageContentEvent: ({ event, textMessageBuffer }) => {
        options.onContent?.(`${textMessageBuffer}${event.delta}`)
      },
      onTextMessageEndEvent: ({ textMessageBuffer }) => {
        options.onContent?.(textMessageBuffer)
      },
      onRunErrorEvent: ({ event }) => {
        // RUN_ERROR 是运行失败终态；HttpAgent 不会自动 reject，需要在会话边界显式抛出。
        runErrorMessage = event.message || 'Workflow 运行失败，请重试。'
        runErrorCode = event.code
      },
      onToolCallStartEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'start', event))
      },
      onToolCallArgsEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'args', event))
      },
      onToolCallEndEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'end', event))
      },
      onToolCallResultEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'result', event))
      }
    }

    const runId = randomUUID()
    this.activeAgent = requestAgent
    this.activeRunId = runId
    this.activeRunCompletion = new Promise((resolve) => {
      this.resolveActiveRunCompletion = resolve
    })
    let result: Awaited<ReturnType<HttpAgent['runAgent']>>
    try {
      result = await requestAgent.runAgent(
        {
          runId,
          forwardedProps: buildWorkflowForwardedProps(options)
        },
        subscriber
      )
    } finally {
      if (this.activeAgent === requestAgent) {
        this.activeAgent = undefined
        this.activeRunId = undefined
        this.resolveActiveRunCompletion?.()
        this.activeRunCompletion = undefined
        this.resolveActiveRunCompletion = undefined
      }
    }
    if (runErrorMessage) {
      throw new AgUiRunError(runErrorMessage, {
        code: runErrorCode,
        workflow,
        toolCalls,
        processSteps
      })
    }
    const assistantMessage = result.newMessages.find(
      (newMessage) => newMessage.role === 'assistant'
    )
    workflow = readResultWorkflow(result.result) ?? workflow
    if (workflow) emitWorkflowLifecycle(workflow, options.onApplicationLifecycle)
    const answer =
      messageContentToText(assistantMessage?.content).trim() ||
      workflow?.summary?.message ||
      'Workflow run finished.'

    return {
      threadId: this.threadId,
      runId,
      answer,
      workflow,
      toolCalls,
      processSteps,
      assistantMessage
    }
  }

  /** 通过当前会话的实际端点发送独立取消控制请求，并返回后端是否接管取消。 */
  private async cancelRun(targetRunId: string): Promise<boolean> {
    const cancellationAgent = createAgUiHttpAgent({
      url: this.endpointUrl,
      threadId: this.threadId
    })
    try {
      const result = await cancellationAgent.runAgent({
        forwardedProps: { cancelRunId: targetRunId }
      })
      const control = objectValue(objectValue(result.result).workflowRunControl)
      return stringValue(control.status) === 'cancel_requested'
    } catch {
      return false
    }
  }
}

function mergeProcessStep(
  steps: ProcessStepRecord[],
  step: ProcessStepRecord
): ProcessStepRecord[] {
  const existingIndex = steps.findIndex((item) => item.id === step.id)
  const existing = existingIndex >= 0 ? steps[existingIndex] : undefined
  const dagGeneration = newerDagGenerationSnapshot(existing?.dagGeneration, step.dagGeneration)
  const mergedStep = {
    ...existing,
    ...step,
    detail: step.appendDetail
      ? `${existing?.detail || ''}${step.detail}`.slice(-24_000)
      : step.detail,
    appendDetail: false,
    sequence: existing?.sequence ?? step.sequence,
    ...(dagGeneration ? { dagGeneration } : {})
  }
  const next =
    existingIndex < 0
      ? [...steps, mergedStep]
      : steps.map((item, index) => (index === existingIndex ? mergedStep : item))
  return next.sort((left, right) => left.sequence - right.sequence)
}

/** 同一 PlanningRun 仅接受 revision 不低于当前值的完整快照。 */
export function newerDagGenerationSnapshot(
  current: DagGenerationSnapshot | undefined,
  incoming: DagGenerationSnapshot | undefined
): DagGenerationSnapshot | undefined {
  if (!current) return incoming
  if (!incoming) return current
  if (current.planningRunId !== incoming.planningRunId) return incoming
  return incoming.revision >= current.revision ? incoming : current
}

/** 解析后端传来的流程步骤，并忽略不符合协议的扩展字段。 */
function readProcessStep(value: unknown): ProcessStepRecord | undefined {
  const step = objectValue(value)
  const id = stringValue(step.id)
  const kind = stringValue(step.kind)
  const status = stringValue(step.status)
  if (!id || !['reasoning', 'tool', 'command', 'workflow'].includes(kind)) return undefined
  if (!['running', 'completed', 'failed', 'requires_user_input'].includes(status)) return undefined
  const checks = readIntegrationTestChecks(step.checks)
  const dagGeneration = readDagGenerationSnapshot(step.dagGeneration)
  const workspaceInspection = readWorkspaceInspectionSnapshot(step.workspaceInspection)
  const workspaceInspectionProgress = readWorkspaceInspectionProgress(
    step.workspaceInspectionProgress
  )
  return {
    id,
    kind: kind as ProcessStepRecord['kind'],
    status: status as ProcessStepRecord['status'],
    title: stringValue(step.title),
    detail: stringValue(step.detail),
    result: stringValue(step.result) || undefined,
    sequence: typeof step.sequence === 'number' ? step.sequence : 0,
    appendDetail: step.appendDetail === true,
    nodeName: stringValue(step.nodeName) || undefined,
    attempt: typeof step.attempt === 'number' ? step.attempt : undefined,
    iterationKind: stringValue(step.iterationKind) || undefined,
    buildExecutionSlice:
      step.buildExecutionSlice && typeof step.buildExecutionSlice === 'object'
        ? (step.buildExecutionSlice as import('../typings').WorkflowBuildExecutionSlice)
        : undefined,
    ...(checks ? { checks } : {}),
    ...(dagGeneration ? { dagGeneration } : {}),
    ...(workspaceInspection ? { workspaceInspection } : {}),
    ...(workspaceInspectionProgress ? { workspaceInspectionProgress } : {})
  }
}

/** 解析代码图扫描中的实时计数，供运行中的流程步骤原位更新。 */
function readWorkspaceInspectionProgress(value: unknown): WorkspaceInspectionProgress | undefined {
  const progress = objectValue(value)
  if (!Object.keys(progress).length) return undefined
  return {
    stage: boundedString(progress.stage, 80),
    status: boundedString(progress.status, 40),
    message: boundedString(progress.message, 300),
    filesDiscovered: nonNegativeInteger(progress.filesDiscovered),
    filesIndexed: nonNegativeInteger(progress.filesIndexed),
    symbolsIndexed: nonNegativeInteger(progress.symbolsIndexed),
    relationsIndexed: nonNegativeInteger(progress.relationsIndexed),
    cacheHit: progress.cacheHit === true
  }
}

/** 解析工作区检查快照，并兼容历史状态使用的 snake_case 字段。 */
export function readWorkspaceInspectionSnapshot(
  value: unknown
): WorkspaceInspectionSnapshot | undefined {
  const snapshot = objectValue(parseStructuredValue(value))
  const manifest = objectValue(snapshot.fileManifest ?? snapshot.file_manifest)
  const codeGraph = objectValue(snapshot.codeGraph ?? snapshot.code_graph)
  const projectRoots = readWorkspacePathItems(snapshot.projectRoots ?? snapshot.project_roots, 40)
  const entrypoints = readWorkspacePathItems(snapshot.entrypoints, 80)
  const schemaVersion = boundedString(snapshot.schemaVersion ?? snapshot.schema_version, 80)
  const revision = boundedString(snapshot.revision ?? snapshot.workspace_revision, 80)
  const hasManifest = Object.keys(manifest).length > 0
  if (!schemaVersion && !revision && !hasManifest && projectRoots.length === 0) return undefined

  return {
    schemaVersion,
    revision,
    cacheHit: snapshot.cacheHit === true || snapshot.cache_hit === true,
    fileManifest: {
      totalFiles: nonNegativeInteger(manifest.totalFiles ?? manifest.total_files_indexed),
      sourceFiles: nonNegativeInteger(manifest.sourceFiles ?? manifest.source_files_indexed),
      truncated: manifest.truncated === true
    },
    techStack: boundedStringList(snapshot.techStack ?? snapshot.tech_stack, 40, 160),
    projectRoots,
    entrypoints,
    codeGraph: {
      provider: boundedString(codeGraph.provider, 80) || 'none',
      providerVersion: boundedString(codeGraph.providerVersion, 40) || undefined,
      status: boundedString(codeGraph.status, 40) || undefined,
      available: codeGraph.available === true,
      buildType: boundedString(codeGraph.buildType, 40) || undefined,
      filesIndexed: optionalNonNegativeInteger(codeGraph.filesIndexed),
      symbolsIndexed: optionalNonNegativeInteger(codeGraph.symbolsIndexed),
      relationsIndexed: optionalNonNegativeInteger(codeGraph.relationsIndexed),
      languages: boundedStringList(codeGraph.languages, 20, 40),
      nodesByKind: readCodeGraphDistributions(codeGraph.nodesByKind ?? codeGraph.nodes_by_kind),
      relationsByKind: readCodeGraphDistributions(
        codeGraph.relationsByKind ?? codeGraph.relations_by_kind
      ),
      sampleSymbols: readCodeGraphSymbols(codeGraph.sampleSymbols ?? codeGraph.sample_symbols),
      warningCount: optionalNonNegativeInteger(codeGraph.warningCount ?? codeGraph.warning_count),
      warnings: boundedStringList(codeGraph.warnings, 5, 240),
      message: boundedString(codeGraph.message, 300) || undefined,
      durationMs: optionalNonNegativeInteger(codeGraph.durationMs),
      cacheHit: codeGraph.cacheHit === true
    }
  }
}

/** 解析 CRG 节点或关系的分类统计，并限制展示条数。 */
function readCodeGraphDistributions(value: unknown): CodeGraphDistribution[] {
  if (!Array.isArray(value)) return []
  return value.slice(0, 12).flatMap((item) => {
    const candidate = objectValue(item)
    const kind = boundedString(candidate.kind, 80)
    if (!kind) return []
    return [{ kind, count: nonNegativeInteger(candidate.count) }]
  })
}

/** 解析代表性符号预览，只保留相对路径和行号。 */
function readCodeGraphSymbols(value: unknown): CodeGraphSymbolPreview[] {
  if (!Array.isArray(value)) return []
  return value.slice(0, 8).flatMap((item) => {
    const candidate = objectValue(item)
    const path = readWorkspaceRelativePath(candidate.path)
    if (!path) return []
    return [
      {
        name: boundedString(candidate.name, 200),
        kind: boundedString(candidate.kind, 80),
        language: boundedString(candidate.language, 40),
        path,
        lineStart: nonNegativeInteger(candidate.lineStart ?? candidate.line_start),
        lineEnd: nonNegativeInteger(candidate.lineEnd ?? candidate.line_end)
      }
    ]
  })
}

/** 仅接受工作区相对路径，防止历史或未知事件把宿主机路径带入界面。 */
function readWorkspacePathItems(value: unknown, limit: number): WorkspaceInspectionPathItem[] {
  if (!Array.isArray(value)) return []
  const items: WorkspaceInspectionPathItem[] = []
  for (const item of value) {
    const candidate = objectValue(item)
    const path = readWorkspaceRelativePath(candidate.path)
    if (!path) continue
    items.push({
      path,
      kind: boundedString(candidate.kind, 80) || 'unknown'
    })
    if (items.length >= limit) break
  }
  return items
}

/** 仅接受工作区相对路径，过滤绝对路径、盘符路径和路径穿越。 */
function readWorkspaceRelativePath(value: unknown): string {
  const path = boundedString(value, 1_000).replaceAll('\\', '/')
  if (!path || path.startsWith('/') || /^[a-z]:\//i.test(path) || path.split('/').includes('..')) {
    return ''
  }
  return path
}

/** 解析 PlanningRun 完整进度快照，并拒绝旧版阶段结构和未知枚举。 */
export function readDagGenerationSnapshot(value: unknown): DagGenerationSnapshot | undefined {
  const snapshot = objectValue(parseStructuredValue(value))
  const schemaVersion = boundedString(snapshot.schemaVersion, 80)
  const planningRunId = boundedString(snapshot.planningRunId, 240)
  const status = stringValue(snapshot.status)
  const phase = stringValue(snapshot.phase)
  if (
    schemaVersion !== 'dag-generation.v1' ||
    !planningRunId ||
    !Number.isInteger(snapshot.revision) ||
    Number(snapshot.revision) < 0 ||
    !DAG_GENERATION_RUN_STATUSES.has(status) ||
    !DAG_GENERATION_PHASES.has(phase) ||
    !Array.isArray(snapshot.units) ||
    !snapshot.summary ||
    typeof snapshot.summary !== 'object'
  ) {
    return undefined
  }

  const units = readDagGenerationProgressUnits(snapshot.units)
  if (!units) return undefined

  return {
    schemaVersion,
    planningRunId,
    revision: nonNegativeInteger(snapshot.revision),
    status: status as DagGenerationRunStatus,
    phase: phase as DagGenerationPhase,
    globalRepairRound: nonNegativeInteger(snapshot.globalRepairRound),
    globalRepairLimit: nonNegativeInteger(snapshot.globalRepairLimit),
    units,
    globalIssues: readDagGenerationIssues(snapshot.globalIssues),
    summary: readDagGenerationSummary(snapshot.summary)
  }
}

/** 完整校验全部 Unit 的生命周期枚举，再单独截取 UI 可展示的前 200 条。 */
function readDagGenerationProgressUnits(value: unknown): DagGenerationUnitRecord[] | undefined {
  if (!Array.isArray(value)) return undefined
  const units: DagGenerationUnitRecord[] = []
  for (const item of value) {
    const unit = objectValue(item)
    const id = boundedString(unit.id, 240)
    const participation = stringValue(unit.participation)
    const generationStrategy = stringValue(unit.generationStrategy)
    const status = stringValue(unit.status)
    if (
      !id ||
      !DAG_GENERATION_PARTICIPATIONS.has(participation) ||
      !DAG_GENERATION_STRATEGIES.has(generationStrategy) ||
      !DAG_GENERATION_UNIT_STATUSES.has(status)
    ) {
      return undefined
    }
    if (units.length < 200) {
      units.push({
        id,
        kind: boundedString(unit.kind, 80) || 'unknown',
        participation: participation as DagGenerationParticipation,
        generationStrategy: generationStrategy as DagGenerationStrategy,
        status: status as DagGenerationUnitStatus,
        generationRound: nonNegativeInteger(unit.generationRound),
        attemptInRound: nonNegativeInteger(unit.attemptInRound),
        localAttemptLimit: nonNegativeInteger(unit.localAttemptLimit),
        totalAttempts: nonNegativeInteger(unit.totalAttempts),
        retainedTaskCount: nonNegativeInteger(unit.retainedTaskCount),
        reusableCapabilityCount: nonNegativeInteger(unit.reusableCapabilityCount),
        candidateTaskCount: nonNegativeInteger(unit.candidateTaskCount),
        issues: readDagGenerationIssues(unit.issues)
      })
    }
  }
  return units
}

/** 解析安全 Issue 投影；Candidate 身份、正文和任意 details 均不会进入前端状态。 */
function readDagGenerationIssues(value: unknown): DagGenerationIssueRecord[] {
  if (!Array.isArray(value)) return []
  return value.slice(0, 100).flatMap((item) => {
    const issue = objectValue(item)
    const code = boundedString(issue.code, 160)
    const message = boundedString(issue.message, 1_000)
    if (!code || !message) return []
    return [
      {
        code,
        level: boundedString(issue.level, 80) || 'unit',
        category: boundedString(issue.category, 80) || 'validation',
        unitIds: boundedStringList(issue.unitIds, 200, 240),
        retryUnitIds: boundedStringList(issue.retryUnitIds, 200, 240),
        retryable: issue.retryable === true,
        message
      }
    ]
  })
}

/** 解析服务端离散计数摘要，不计算或补造百分比。 */
function readDagGenerationSummary(value: unknown): DagGenerationSummary {
  const summary = objectValue(value)
  return {
    unitCount: nonNegativeInteger(summary.unitCount),
    readyUnitCount: nonNegativeInteger(summary.readyUnitCount),
    pendingUnitCount: nonNegativeInteger(summary.pendingUnitCount),
    activeUnitCount: nonNegativeInteger(summary.activeUnitCount),
    roundExhaustedUnitCount: nonNegativeInteger(summary.roundExhaustedUnitCount),
    abortedUnitCount: nonNegativeInteger(summary.abortedUnitCount),
    retainedTaskCount: nonNegativeInteger(summary.retainedTaskCount),
    candidateTaskCount: nonNegativeInteger(summary.candidateTaskCount),
    unitIssueCount: nonNegativeInteger(summary.unitIssueCount),
    globalIssueCount: nonNegativeInteger(summary.globalIssueCount),
    failureIssueCount: nonNegativeInteger(summary.failureIssueCount)
  }
}

/** 校验并裁剪页面细节确认产生的只读项目计划更新快照。 */
export function readProjectPlanUpdate(value: unknown): WorkflowProjectPlanUpdate | undefined {
  const update = objectValue(value)
  const targetType = stringValue(update.targetType)
  if (
    update.format !== 'markdown' ||
    update.readOnly !== true ||
    update.status !== 'confirmed' ||
    !['page', 'endpoint'].includes(targetType) ||
    !Array.isArray(update.sections)
  ) {
    return undefined
  }

  const sections = update.sections
    .flatMap((item) => {
      const section = objectValue(item)
      const kind = stringValue(section.kind)
      const id = boundedString(section.id, 500)
      const title = boundedString(section.title, 500)
      const content = boundedString(section.content, 64_000)
      if (!id || !title || !content || !['page', 'endpoint'].includes(kind)) return []
      return [
        {
          id,
          kind: kind as 'page' | 'endpoint',
          title,
          subtitle: boundedString(section.subtitle, 1_000) || undefined,
          content
        }
      ]
    })
    .slice(0, 64)
  const summary = objectValue(update.summary)
  const documentName = boundedString(update.documentName, 500)
  const targetId = boundedString(update.targetId, 500)
  if (!documentName || !targetId || sections.length === 0) return undefined

  return {
    format: 'markdown',
    readOnly: true,
    documentName,
    status: 'confirmed',
    targetType: targetType as 'page' | 'endpoint',
    targetId,
    summary: {
      pageCount: nonNegativeInteger(summary.pageCount),
      endpointCount: nonNegativeInteger(summary.endpointCount)
    },
    sections
  }
}

/** 解析集成测试的安全检查快照，避免未知事件内容直接进入 UI。 */
export function readIntegrationTestChecks(
  value: unknown
): IntegrationTestCheckRecord[] | undefined {
  const normalizedValue = parseStructuredValue(value)
  const checksValue = Array.isArray(normalizedValue)
    ? normalizedValue
    : objectValue(normalizedValue).checks
  if (!Array.isArray(checksValue)) return undefined
  const seenIds = new Set<string>()
  const checks: IntegrationTestCheckRecord[] = []
  for (const item of checksValue) {
    if (!item || typeof item !== 'object') continue
    const check = item as Record<string, unknown>
    const id = stringValue(check.id)
    const name = stringValue(check.name)
    const declaredStatus = stringValue(check.status)
    const status = ['running', 'passed', 'skipped', 'failed'].includes(declaredStatus)
      ? declaredStatus
      : check.skipped === true && check.passed === true
        ? 'skipped'
        : check.passed === true
          ? 'passed'
          : 'failed'
    if (!id || !name || seenIds.has(id)) continue
    seenIds.add(id)
    const evidence = stringValue(check.evidence).slice(0, 1_000)
    const passedTests = optionalNonNegativeInteger(check.passedTests ?? check.passed_tests)
    const totalTests = optionalNonNegativeInteger(check.totalTests ?? check.total_tests)
    checks.push({
      id,
      name,
      status: status as IntegrationTestCheckRecord['status'],
      required: check.required === true,
      ...(check.advisory === true ? { advisory: true } : {}),
      ...(evidence ? { evidence } : {}),
      ...(passedTests !== undefined ? { passedTests } : {}),
      ...(totalTests !== undefined ? { totalTests } : {})
    })
    const performanceScores = readPerformanceScores(
      check.performanceScores ?? check.performance_scores
    )
    if (Object.keys(performanceScores).length > 0) {
      checks[checks.length - 1] = {
        ...checks[checks.length - 1],
        performanceScores
      }
    }
    const performanceMetrics = readPerformanceMetrics(
      check.performanceMetrics ?? check.performance_metrics
    )
    if (Object.keys(performanceMetrics).length > 0) {
      checks[checks.length - 1] = {
        ...checks[checks.length - 1],
        performanceMetrics
      }
    }
    const reportPath = stringValue(check.reportPath ?? check.report_path).slice(0, 1_000)
    if (reportPath) {
      checks[checks.length - 1] = {
        ...checks[checks.length - 1],
        reportPath
      }
    }
  }
  return checks.length > 0 ? checks : undefined
}

/** 解析 AG-UI 事件中可能被序列化的结构化扩展字段。 */
function parseStructuredValue(value: unknown): unknown {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function applyToolCallEvent(
  toolCalls: ToolCallRecord[],
  eventType: 'start' | 'args' | 'end' | 'result',
  event: unknown
): ToolCallRecord[] {
  const eventObject = objectValue(event)
  const id = stringValue(eventObject.toolCallId ?? eventObject.tool_call_id)
  if (!id) return toolCalls

  const existing = toolCalls.find((toolCall) => toolCall.id === id)
  const name = stringValue(eventObject.toolCallName ?? eventObject.tool_call_name)
  const delta = stringValue(eventObject.delta)
  const content = stringValue(eventObject.content)
  const nextToolCall: ToolCallRecord = {
    id,
    name: name || existing?.name || 'unknown',
    args: existing?.args || '',
    result: existing?.result,
    status: existing?.status || 'running'
  }

  if (eventType === 'args') {
    nextToolCall.args += delta
  }

  if (eventType === 'result') {
    nextToolCall.result = content
    nextToolCall.status = 'completed'
  }

  const updatedToolCalls = existing
    ? toolCalls.map((toolCall) => (toolCall.id === id ? nextToolCall : toolCall))
    : [...toolCalls, nextToolCall]
  return updatedToolCalls
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** 裁剪单个协议字符串，避免不可信事件撑大持久化消息。 */
function boundedString(value: unknown, limit: number): string {
  return stringValue(value).trim().slice(0, limit)
}

/** 裁剪、去重协议字符串列表，并限制最大条数。 */
function boundedStringList(value: unknown, itemLimit: number, textLimit: number): string[] {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map((item) => boundedString(item, textLimit)).filter(Boolean))].slice(
    0,
    itemLimit
  )
}

/** 把协议摘要字段安全转换为非负整数。 */
function nonNegativeInteger(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(1_000_000, Math.max(0, Math.trunc(value)))
    : 0
}

/** 保留缺失字段，避免把未完成索引伪装成零值统计。 */
function optionalNonNegativeInteger(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(1_000_000, Math.max(0, Math.trunc(value)))
    : undefined
}

/** 读取 Lighthouse 分类得分，只保留 0-100 的合法整数。 */
function readPerformanceScores(
  value: unknown
): NonNullable<IntegrationTestCheckRecord['performanceScores']> {
  const source = objectValue(value)
  const scores: NonNullable<IntegrationTestCheckRecord['performanceScores']> = {}
  const allowed = [
    'performance',
    'accessibility',
    'bestPractices',
    'best_practices',
    'seo'
  ] as const
  for (const key of allowed) {
    const raw = source[key]
    if (typeof raw !== 'number' || !Number.isFinite(raw)) continue
    const normalized = Math.min(100, Math.max(0, Math.round(raw)))
    const outputKey = key === 'bestPractices' || key === 'best_practices' ? 'bestPractices' : key
    scores[outputKey as keyof typeof scores] = normalized
  }
  return scores
}

/** 读取 Lighthouse 核心指标，只保留有限数值。 */
function readPerformanceMetrics(
  value: unknown
): NonNullable<IntegrationTestCheckRecord['performanceMetrics']> {
  const source = objectValue(value)
  const metrics: NonNullable<IntegrationTestCheckRecord['performanceMetrics']> = {}
  const allowed = ['fcp', 'lcp', 'tbt', 'cls', 'si'] as const
  for (const key of allowed) {
    const raw = source[key]
    if (typeof raw !== 'number' || !Number.isFinite(raw)) continue
    metrics[key] = raw
  }
  return metrics
}

function messageContentToText(content: Message['content'] | undefined): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === 'string') return item
        if ('text' in item && typeof item.text === 'string') return item.text
        return ''
      })
      .filter(Boolean)
      .join('\n')
  }
  return ''
}

function readWorkflowFromState(snapshot: unknown): WorkflowRunPayload | undefined {
  if (!snapshot || typeof snapshot !== 'object') return undefined
  const value = snapshot as { workflow?: unknown; conversation?: unknown }
  return readWorkflowPayload(value.workflow) ?? readWorkflowPayload(value.conversation)
}

function readResultWorkflow(result: unknown): WorkflowRunPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  const value = result as { workflow?: unknown; conversation?: unknown }
  return readWorkflowPayload(value.workflow) ?? readWorkflowPayload(value.conversation)
}

function readWorkflowPayload(value: unknown): WorkflowRunPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<WorkflowRunPayload>
  if (typeof payload.runId !== 'string' || typeof payload.threadId !== 'string') {
    return undefined
  }
  const state =
    payload.state && typeof payload.state === 'object'
      ? (payload.state as Record<string, unknown>)
      : undefined
  const codeChanges =
    readCodeChangesPayload(payload.codeChanges) ?? readCodeChangesPayload(state?.codeChanges)

  return {
    runId: payload.runId,
    threadId: payload.threadId,
    summary:
      payload.summary && typeof payload.summary === 'object'
        ? payload.summary
        : { status: 'unknown' },
    events: Array.isArray(payload.events) ? payload.events.map(readWorkflowEvent) : [],
    confirmationArtifact: readConfirmationArtifact(payload.confirmationArtifact),
    codeChanges,
    state:
      payload.state && typeof payload.state === 'object'
        ? (payload.state as Record<string, unknown>)
        : undefined,
    result:
      payload.result && typeof payload.result === 'object'
        ? (payload.result as Record<string, unknown>)
        : undefined
  }
}

/** 校验独立 lifecycle 事件的最小稳定字段，忽略未知或损坏的实时投影。 */
export function readApplicationLifecycle(value: unknown): ApplicationLifecycle | undefined {
  if (!value || typeof value !== 'object') return undefined
  const lifecycle = value as Partial<ApplicationLifecycle>
  if (
    typeof lifecycle.revision !== 'number' ||
    !lifecycle.application ||
    typeof lifecycle.application.id !== 'string' ||
    !lifecycle.initialization ||
    typeof lifecycle.initialization.stage !== 'string' ||
    !lifecycle.activeExecutions ||
    typeof lifecycle.activeExecutions !== 'object'
  ) {
    return undefined
  }
  return lifecycle as ApplicationLifecycle
}

/** 从兼容 Workflow 投影中同步 lifecycle，避免旧后端缺少独立事件时丢失校准。 */
function emitWorkflowLifecycle(
  workflow: WorkflowRunPayload,
  listener?: (lifecycle: ApplicationLifecycle) => void
): void {
  const lifecycle =
    readApplicationLifecycle(workflow.summary.lifecycle) ??
    readApplicationLifecycle(workflow.state?.lifecycle) ??
    readApplicationLifecycle(workflow.result?.lifecycle)
  if (lifecycle) listener?.(lifecycle)
}

function readConfirmationArtifact(value: unknown): WorkflowConfirmationArtifact | undefined {
  if (!value || typeof value !== 'object') return undefined
  const artifact = value as Partial<WorkflowConfirmationArtifact>
  if (
    !['requirement_spec', 'product_plan', 'technical_plan', 'project_plan'].includes(
      String(artifact.id)
    )
  )
    return undefined
  if (artifact.format !== 'markdown') return undefined
  if (
    typeof artifact.name !== 'string' ||
    typeof artifact.path !== 'string' ||
    typeof artifact.content !== 'string'
  ) {
    return undefined
  }
  return artifact as WorkflowConfirmationArtifact
}

function readCodeChangesPayload(value: unknown): WorkspaceCodeChangeSet | undefined {
  if (!value || typeof value !== 'object') return undefined
  const codeChanges = value as Partial<WorkspaceCodeChangeSet>
  if (!Array.isArray(codeChanges.files) || codeChanges.files.length === 0) return undefined
  if (!codeChanges.summary || typeof codeChanges.summary !== 'object') return undefined
  return codeChanges as WorkspaceCodeChangeSet
}

function readWorkflowEvent(value: unknown): WorkflowEvent {
  if (!value || typeof value !== 'object') return { type: 'workflow.event' }
  const event = value as WorkflowEvent
  const node = event.node && typeof event.node === 'object' ? event.node : undefined
  return {
    ...event,
    node,
    nodeName: event.nodeName || node?.id
  }
}

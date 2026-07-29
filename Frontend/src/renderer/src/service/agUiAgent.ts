import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber, HttpAgent } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowConfirmationArtifact,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowEvent,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../typings'

export type SendWorkflowMessageOptions = {
  workspaceRoot?: string
  editorMode: EditorMode
  application?: ApplicationConfig
  clarificationAnswers?: WorkflowClarificationAnswers
  editedRequirementSpec?: Record<string, unknown>
  requirementSpecFeedback?: string
  originalRequest?: string
  selectedSkillNames?: string[]
  selectedPageId?: string
  selectedApiContractId?: string
  selectedEndpointId?: string
  detailTargetType?: 'page' | 'endpoint'
  buildExecutionScope?: WorkflowBuildExecutionScope
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
  directModification?: boolean
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
    editedRequirementSpec: options.editedRequirementSpec,
    requirementSpecFeedback: options.requirementSpecFeedback,
    originalRequest: options.originalRequest,
    selectedSkillNames: options.selectedSkillNames,
    selectedPageId: options.selectedPageId,
    selectedApiContractId: options.selectedApiContractId,
    selectedEndpointId: options.selectedEndpointId,
    detailTargetType: options.detailTargetType,
    workflowDebug: options.workflowDebug,
    resumeFrom: options.workflowDebug?.enabled ? options.workflowDebug.resumeFrom : undefined,
    buildExecutionScope: options.buildExecutionScope || (
      options.workflowDebug?.enabled
        ? options.workflowDebug.buildExecutionScope
        : undefined
    ),
    resumeState: options.resumeState,
    workflowScope: options.workflowScope,
    planControlAction: options.planControlAction,
    planControlRunId: options.planControlRunId,
    resumeExecutionRunId: options.resumeExecutionRunId,
    pageTemplate: options.pageTemplate,
    directModification: options.directModification
      ? {
          workspaceRoot: options.workspaceRoot,
          selectedSkillNames: options.selectedSkillNames
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
  evidence?: string
}

export type DagGenerationStageRecord = {
  id: string
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  detail: string
}

export type DagGenerationTaskRecord = {
  id: string
  title: string
  owner: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  dependencies: string[]
  changePaths: string[]
  acceptanceCriteria: string[]
}

export type DagGenerationArtifactRecord = {
  id: string
  name: string
  kind: 'internal' | 'markdown'
  status: 'saved'
  path?: string
}

export type DagGenerationSnapshot = {
  stages: DagGenerationStageRecord[]
  tasks: DagGenerationTaskRecord[]
  summary: {
    unitCount: number
    taskCount: number
    edgeCount: number
    batchCount: number
    frontendCount: number
    dataSourceCount: number
    isValid: boolean
  }
  artifacts: DagGenerationArtifactRecord[]
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
}

/** 返回主工作流的 AG-UI 地址。 */
export function getWorkflowUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/workflow/run`
    : '/api/agent/workflow/run'
}

/** 返回独立快速修改 Graph 的 AG-UI 地址。 */
export function getDirectModificationUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/direct-modification/run`
    : '/api/agent/direct-modification/run'
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
    let runError: Error | undefined
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
        if (event.name === 'direct-modification') {
          workflow = readWorkflowPayload(event.value) ?? workflow
          const step = readProcessStep(objectValue(event.value).processStep)
          if (step) {
            processSteps = mergeProcessStep(processSteps, step)
            options.onProcessSteps?.(processSteps)
          }
          if (workflow) options.onWorkflow?.(workflow)
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
        runError = new Error(event.message || 'Workflow 运行失败，请重试。')
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
    if (runError) throw runError
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
  const mergedStep = {
    ...existing,
    ...step,
    detail: step.appendDetail
      ? `${existing?.detail || ''}${step.detail}`.slice(-24_000)
      : step.detail,
    appendDetail: false,
    sequence: existing?.sequence ?? step.sequence
  }
  const next =
    existingIndex < 0
      ? [...steps, mergedStep]
      : steps.map((item, index) => (index === existingIndex ? mergedStep : item))
  return next.sort((left, right) => left.sequence - right.sequence)
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
    ...(dagGeneration ? { dagGeneration } : {})
  }
}

/** 解析 DAG 生成快照，仅保留前端展示所需的受限结构。 */
export function readDagGenerationSnapshot(value: unknown): DagGenerationSnapshot | undefined {
  const snapshot = objectValue(parseStructuredValue(value))
  if (!Array.isArray(snapshot.stages)) return undefined

  const stages = snapshot.stages.flatMap((item) => {
    const stage = objectValue(item)
    const id = boundedString(stage.id, 240)
    const name = boundedString(stage.name, 500)
    const status = stringValue(stage.status)
    if (!id || !name || !['pending', 'running', 'completed', 'failed'].includes(status)) return []
    return [
      {
        id,
        name,
        status: status as DagGenerationStageRecord['status'],
        detail: boundedString(stage.detail, 1_000)
      }
    ]
  })
  if (stages.length === 0) return undefined

  const tasks = Array.isArray(snapshot.tasks)
    ? snapshot.tasks.flatMap((item) => {
        const task = objectValue(item)
        const id = boundedString(task.id, 240)
        const title = boundedString(task.title, 500)
        const status = stringValue(task.status)
        if (!id || !title || !['pending', 'running', 'completed', 'failed'].includes(status)) {
          return []
        }
        return [
          {
            id,
            title,
            owner: boundedString(task.owner, 80),
            status: status as DagGenerationTaskRecord['status'],
            dependencies: boundedStringList(task.dependencies, 200, 240),
            changePaths: boundedStringList(task.changePaths, 200, 1_000),
            acceptanceCriteria: boundedStringList(task.acceptanceCriteria, 100, 1_000)
          }
        ]
      })
    : []
  const summary = objectValue(snapshot.summary)
  const artifacts = Array.isArray(snapshot.artifacts)
    ? snapshot.artifacts.flatMap((item) => {
        const artifact = objectValue(item)
        const id = boundedString(artifact.id, 240)
        const name = boundedString(artifact.name, 500)
        const kind = stringValue(artifact.kind)
        if (!id || !name || !['internal', 'markdown'].includes(kind)) return []
        return [
          {
            id,
            name,
            kind: kind as DagGenerationArtifactRecord['kind'],
            status: 'saved' as const,
            ...(boundedString(artifact.path, 1_000)
              ? { path: boundedString(artifact.path, 1_000) }
              : {})
          }
        ]
      })
    : []

  return {
    stages,
    tasks,
    summary: {
      unitCount: nonNegativeInteger(summary.unitCount),
      taskCount: nonNegativeInteger(summary.taskCount),
      edgeCount: nonNegativeInteger(summary.edgeCount),
      batchCount: nonNegativeInteger(summary.batchCount),
      frontendCount: nonNegativeInteger(summary.frontendCount),
      dataSourceCount: nonNegativeInteger(summary.dataSourceCount),
      isValid: summary.isValid === true
    },
    artifacts
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
    checks.push({
      id,
      name,
      status: status as IntegrationTestCheckRecord['status'],
      required: check.required === true,
      ...(evidence ? { evidence } : {})
    })
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
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0
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
  const value = snapshot as { workflow?: unknown; directModification?: unknown }
  return readWorkflowPayload(value.workflow) ?? readWorkflowPayload(value.directModification)
}

function readResultWorkflow(result: unknown): WorkflowRunPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  const value = result as { workflow?: unknown; directModification?: unknown }
  return readWorkflowPayload(value.workflow) ?? readWorkflowPayload(value.directModification)
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
    lifecycle.schemaVersion !== '1.2.0' ||
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
  if (!['requirement_spec', 'project_plan'].includes(String(artifact.id))) return undefined
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

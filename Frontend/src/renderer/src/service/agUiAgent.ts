import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber, HttpAgent } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent, isAuthenticationFailure } from './authentication'
import type {
  ApplicationConfig,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowConfirmationArtifact,
  WorkflowDebugOptions,
  WorkflowEvent,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../typings'

type SendWorkflowMessageOptions = {
  workspaceRoot?: string
  editorMode: EditorMode
  application?: ApplicationConfig
  clarificationAnswers?: WorkflowClarificationAnswers
  originalRequest?: string
  selectedSkillNames?: string[]
  workflowDebug?: WorkflowDebugOptions
  resumeState?: WorkflowRunPayload
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onToolCalls?: (toolCalls: ToolCallRecord[]) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

/** 构建 `/workflow/run` 的 AG-UI forwardedProps，集中维护技能字段位置。 */
export function buildWorkflowForwardedProps(
  options: SendWorkflowMessageOptions
): Record<string, unknown> {
  return {
    workspaceRoot: options.workspaceRoot,
    editorMode: options.editorMode,
    application: options.application,
    clarificationAnswers: options.clarificationAnswers,
    originalRequest: options.originalRequest,
    selectedSkillNames: options.selectedSkillNames,
    workflowDebug: options.workflowDebug,
    resumeFrom: options.workflowDebug?.enabled ? options.workflowDebug.resumeFrom : undefined,
    resumeState: options.resumeState
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

export type ProcessStepRecord = {
  id: string
  kind: 'reasoning' | 'tool' | 'command' | 'workflow'
  status: 'running' | 'completed' | 'failed'
  title: string
  detail: string
  result?: string
  sequence: number
  appendDetail?: boolean
}

function getWorkflowUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/workflow/run`
    : '/api/agent/workflow/run'
}

export class AgUiChatSession {
  readonly threadId: string

  private readonly agent: HttpAgent
  private activeRunId?: string

  constructor(threadId = randomUUID()) {
    this.threadId = threadId
    this.agent = createAgUiHttpAgent({
      url: getWorkflowUrl(),
      threadId
    })
  }

  stop(): void {
    const runId = this.activeRunId
    this.agent.abortRun()
    if (runId) void this.cancelRun(runId)
  }

  /** 发送 Workflow 消息，并在认证失败时回滚 HttpAgent 内部的未发送消息。 */
  async sendMessage(message: string, options: SendWorkflowMessageOptions): Promise<AgUiChatResult> {
    const userMessageId = randomUUID()
    this.agent.addMessage({
      id: userMessageId,
      role: 'user',
      content: message
    })

    let workflow: WorkflowRunPayload | undefined
    let toolCalls: ToolCallRecord[] = []
    let processSteps: ProcessStepRecord[] = []
    const emitToolCalls = (nextToolCalls: ToolCallRecord[]): void => {
      toolCalls = nextToolCalls
      options.onToolCalls?.(toolCalls)
    }
    const subscriber: AgentSubscriber = {
      onCustomEvent: ({ event }) => {
        if (event.name === 'agent-process') {
          const step = readProcessStep(event.value)
          if (step) {
            processSteps = mergeProcessStep(processSteps, step)
            options.onProcessSteps?.(processSteps)
          }
        }
        if (event.name === 'workflow-run') {
          workflow = readWorkflowPayload(event.value) ?? workflow
          if (workflow) options.onWorkflow?.(workflow)
        }
      },
      onStateSnapshotEvent: ({ event }) => {
        workflow = readWorkflowFromState(event.snapshot) ?? workflow
        if (workflow) options.onWorkflow?.(workflow)
      },
      onTextMessageContentEvent: ({ event, textMessageBuffer }) => {
        options.onContent?.(`${textMessageBuffer}${event.delta}`)
      },
      onTextMessageEndEvent: ({ textMessageBuffer }) => {
        options.onContent?.(textMessageBuffer)
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
    this.activeRunId = runId
    let result: Awaited<ReturnType<HttpAgent['runAgent']>>
    try {
      result = await this.agent.runAgent(
        {
          runId,
          forwardedProps: buildWorkflowForwardedProps(options)
        },
        subscriber
      )
    } catch (error) {
      if (isAuthenticationFailure(error)) {
        this.agent.setMessages(
          this.agent.messages.filter((existingMessage) => existingMessage.id !== userMessageId)
        )
      }
      throw error
    } finally {
      if (this.activeRunId === runId) this.activeRunId = undefined
    }
    const assistantMessage = result.newMessages.find(
      (newMessage) => newMessage.role === 'assistant'
    )
    workflow = readResultWorkflow(result.result) ?? workflow
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

  private async cancelRun(targetRunId: string): Promise<void> {
    const cancellationAgent = createAgUiHttpAgent({
      url: getWorkflowUrl(),
      threadId: this.threadId
    })
    try {
      await cancellationAgent.runAgent({
        forwardedProps: { cancelRunId: targetRunId }
      })
    } catch {
      // The aborted client stream remains a fallback if the cancellation acknowledgement is lost.
    }
  }
}

function mergeProcessStep(steps: ProcessStepRecord[], step: ProcessStepRecord): ProcessStepRecord[] {
  const existingIndex = steps.findIndex((item) => item.id === step.id)
  const existing = existingIndex >= 0 ? steps[existingIndex] : undefined
  const mergedStep = {
    ...existing,
    ...step,
    detail: step.appendDetail ? `${existing?.detail || ''}${step.detail}`.slice(-24_000) : step.detail,
    appendDetail: false,
    sequence: existing?.sequence ?? step.sequence
  }
  const next = existingIndex < 0
    ? [...steps, mergedStep]
    : steps.map((item, index) => (
        index === existingIndex ? mergedStep : item
      ))
  return next.sort((left, right) => left.sequence - right.sequence)
}

function readProcessStep(value: unknown): ProcessStepRecord | undefined {
  const step = objectValue(value)
  const id = stringValue(step.id)
  const kind = stringValue(step.kind)
  const status = stringValue(step.status)
  if (!id || !['reasoning', 'tool', 'command', 'workflow'].includes(kind)) return undefined
  if (!['running', 'completed', 'failed'].includes(status)) return undefined
  return {
    id,
    kind: kind as ProcessStepRecord['kind'],
    status: status as ProcessStepRecord['status'],
    title: stringValue(step.title),
    detail: stringValue(step.detail),
    result: stringValue(step.result) || undefined,
    sequence: typeof step.sequence === 'number' ? step.sequence : 0,
    appendDetail: step.appendDetail === true
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
  return readWorkflowPayload((snapshot as { workflow?: unknown }).workflow)
}

function readResultWorkflow(result: unknown): WorkflowRunPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  return readWorkflowPayload((result as { workflow?: unknown }).workflow)
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

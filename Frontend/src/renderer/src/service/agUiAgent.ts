import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  ApplicationConfig,
  WorkflowDebugOptions,
  WorkflowEvent,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../typings'

type SendWorkflowMessageOptions = {
  workspaceRoot?: string
  application?: ApplicationConfig
  workflowDebug?: WorkflowDebugOptions
  resumeState?: WorkflowRunPayload
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onToolCalls?: (toolCalls: ToolCallRecord[]) => void
}

export type AgUiChatResult = {
  threadId: string
  answer: string
  workflow?: WorkflowRunPayload
  toolCalls: ToolCallRecord[]
  assistantMessage?: Message
}

export type ToolCallRecord = {
  id: string
  name: string
  args: string
  result?: string
  status: 'running' | 'completed'
}

type ToolCallSubscriber = {
  onToolCallStartEvent?: (input: { event: unknown }) => void
  onToolCallArgsEvent?: (input: { event: unknown }) => void
  onToolCallEndEvent?: (input: { event: unknown }) => void
  onToolCallResultEvent?: (input: { event: unknown }) => void
  onToolCallChunkEvent?: (input: { event: unknown }) => void
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

  constructor(threadId = randomUUID()) {
    this.threadId = threadId
    this.agent = new HttpAgent({
      url: getWorkflowUrl(),
      threadId
    })
  }

  stop(): void {
    this.agent.abortRun()
  }

  async sendMessage(message: string, options: SendWorkflowMessageOptions): Promise<AgUiChatResult> {
    this.agent.addMessage({
      id: randomUUID(),
      role: 'user',
      content: message
    })

    let workflow: WorkflowRunPayload | undefined
    let toolCalls: ToolCallRecord[] = []
    const emitToolCalls = (nextToolCalls: ToolCallRecord[]): void => {
      toolCalls = nextToolCalls
      options.onToolCalls?.(toolCalls)
    }
    const subscriber: AgentSubscriber & ToolCallSubscriber = {
      onCustomEvent: ({ event }) => {
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
      onToolCallChunkEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'chunk', event))
      },
      onToolCallEndEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'end', event))
      },
      onToolCallResultEvent: ({ event }) => {
        emitToolCalls(applyToolCallEvent(toolCalls, 'result', event))
      }
    }

    const result = await this.agent.runAgent(
      {
        forwardedProps: {
          workspaceRoot: options.workspaceRoot,
          application: options.application,
          workflowDebug: options.workflowDebug,
          resumeFrom: options.workflowDebug?.enabled ? options.workflowDebug.resumeFrom : undefined,
          resumeState: options.resumeState
        }
      },
      subscriber
    )
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
      answer,
      workflow,
      toolCalls,
      assistantMessage
    }
  }
}

function applyToolCallEvent(
  toolCalls: ToolCallRecord[],
  eventType: 'start' | 'args' | 'chunk' | 'end' | 'result',
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

  if (eventType === 'args' || eventType === 'chunk') {
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

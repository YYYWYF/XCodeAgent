import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { ApplicationConfig, WorkflowRunPayload } from '../typings'

type SendWorkflowMessageOptions = {
  workspaceRoot?: string
  application?: ApplicationConfig
  resumeState?: WorkflowRunPayload
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
}

export type AgUiChatResult = {
  threadId: string
  answer: string
  workflow?: WorkflowRunPayload
  assistantMessage?: Message
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
    const subscriber: AgentSubscriber = {
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
      }
    }

    const result = await this.agent.runAgent(
      {
        forwardedProps: {
          workspaceRoot: options.workspaceRoot,
          application: options.application,
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
      assistantMessage
    }
  }
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

  return {
    runId: payload.runId,
    threadId: payload.threadId,
    summary:
      payload.summary && typeof payload.summary === 'object'
        ? payload.summary
        : { status: 'unknown' },
    events: Array.isArray(payload.events) ? payload.events : [],
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

import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

export type StartIterationPayload = {
  status: 'completed' | 'failed'
  cleared?: boolean
  error?: { type?: string; message?: string }
}

export type StartIterationInput = {
  workspaceRoot: string
  /** 新迭代所在的分支名，仅用于写 AGENTS.md 的迭代标题。 */
  branchName: string
  description: string
}

type IterationServiceAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'start_iteration'
  workspaceRoot?: string
  cleared?: boolean
  error?: { type?: string; message?: string }
}

/** 返回发起新迭代 AG-UI 动作地址。 */
function getIterationServiceUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/iteration-service/run`
    : '/api/agent/iteration-service/run'
}

/** 校验 AG-UI 返回的迭代载荷。 */
function readIterationPayload(value: unknown): IterationServiceAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<IterationServiceAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as IterationServiceAgUiPayload
}

/** 通过 AG-UI 客户端发起新迭代，清空规划产物。 */
export async function startIteration(
  input: StartIterationInput
): Promise<StartIterationPayload> {
  const agent = createAgUiHttpAgent({
    url: getIterationServiceUrl(),
    threadId: randomUUID()
  })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: `发起新迭代（分支 ${input.branchName}），清空规划产物。`
  }
  agent.addMessage(message)

  let iteration: IterationServiceAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'iteration-service') {
        iteration = readIterationPayload(event.value) ?? iteration
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      const snapshot = (event.snapshot as { iterationService?: unknown }).iterationService
      iteration = readIterationPayload(snapshot) ?? iteration
    }
  }

  const result = await agent.runAgent(
    { forwardedProps: { iterationService: { action: 'start_iteration', ...input } } },
    subscriber
  )
  iteration =
    readIterationPayload(
      (result.result as { iterationService?: unknown } | undefined)?.iterationService
    ) ?? iteration
  if (!iteration) throw new Error('迭代服务接口没有返回有效状态。')
  if (iteration.status === 'failed') {
    throw new Error(iteration.error?.message || '发起新迭代失败。')
  }
  return {
    status: iteration.status,
    cleared: iteration.cleared
  }
}

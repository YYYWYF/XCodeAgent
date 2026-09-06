import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

export type AgentRuntimeDebugResult = {
  status: 'running'
  message: string
  ready: boolean
  pid?: number
  managedModelFallbackAvailable: boolean
  runtimeUrl: string
  debugGatewayToken: string
}

type AgentRuntimeDebugAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'start'
  runtime?: AgentRuntimeDebugResult & { failedStage?: string }
  error?: { type?: string; message?: string }
}

/** 返回 Agent Runtime 独立调试动作的 AG-UI 地址。 */
function getAgentRuntimeDebugUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/agent-runtime-debug/run`
    : '/api/agent/agent-runtime-debug/run'
}

/** 校验 Runtime 调试动作返回的稳定 AG-UI 结果。 */
function readAgentRuntimeDebugPayload(
  value: unknown
): AgentRuntimeDebugAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<AgentRuntimeDebugAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as AgentRuntimeDebugAgUiPayload
}

/** 从 AG-UI StateSnapshot 中读取 Runtime 调试结果。 */
function readAgentRuntimeDebugFromState(
  value: unknown
): AgentRuntimeDebugAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readAgentRuntimeDebugPayload(
    (value as { agentRuntimeDebug?: unknown }).agentRuntimeDebug
  )
}

/** 从 HttpAgent 最终结果中读取 Runtime 调试结果。 */
function readAgentRuntimeDebugFromResult(
  value: unknown
): AgentRuntimeDebugAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readAgentRuntimeDebugPayload(
    (value as { agentRuntimeDebug?: unknown }).agentRuntimeDebug
  )
}

/** 启动工作区 agent-runtime，并返回健康检查后的安全状态。 */
export async function startAgentRuntimeDebug(
  workspaceRoot: string
): Promise<AgentRuntimeDebugResult> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getAgentRuntimeDebugUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: '临时启动当前工作区 Agent Runtime。'
  }
  agent.addMessage(message)

  let response: AgentRuntimeDebugAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'agent-runtime-debug') {
        response = readAgentRuntimeDebugPayload(event.value) ?? response
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      response = readAgentRuntimeDebugFromState(event.snapshot) ?? response
    }
  }
  const result = await agent.runAgent(
    {
      forwardedProps: {
        agentRuntimeDebug: { action: 'start', workspaceRoot }
      }
    },
    subscriber
  )
  response = readAgentRuntimeDebugFromResult(result.result) ?? response
  if (!response) throw new Error('Agent Runtime 调试接口没有返回有效状态。')
  if (response.status === 'failed') {
    throw new Error(response.error?.message || 'Agent Runtime 启动失败。')
  }
  if (
    !response.runtime ||
    response.runtime.status !== 'running' ||
    response.runtime.ready !== true ||
    typeof response.runtime.runtimeUrl !== 'string' ||
    !response.runtime.runtimeUrl ||
    typeof response.runtime.debugGatewayToken !== 'string' ||
    !response.runtime.debugGatewayToken
  ) {
    throw new Error('Agent Runtime 未通过健康检查。')
  }
  return response.runtime
}

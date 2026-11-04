import { randomUUID, type AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { readAuthorizationBootstrap, type WorkflowAuthorizationBootstrap } from './authorizationBootstrap'

/** 使用独立 AG-UI 动作查询原操作一次，不轮询、不恢复 Build 或提交建表。 */
export async function refreshBuiltinAuthorization(
  workspaceRoot: string,
  operationId: string
): Promise<WorkflowAuthorizationBootstrap> {
  const base = window.devAgentStudio?.agentBaseUrl
  const agent = createAgUiHttpAgent({
    url: base ? `${base.replace(/\/$/, '')}/builtin-authorization/run` : '/api/agent/builtin-authorization/run',
    threadId: randomUUID()
  })
  let response: { status?: string; authorizationBootstrap?: unknown; error?: { message?: string } } | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'builtin-authorization') response = event.value
    },
    onStateSnapshotEvent: ({ event }) => {
      response = event.snapshot.builtinAuthorization ?? response
    }
  }
  agent.addMessage({ id: randomUUID(), role: 'user', content: '刷新原权限建表操作的执行状态。' })
  await agent.runAgent({ forwardedProps: { builtinAuthorization: { action: 'refresh', workspaceRoot, operationId } } }, subscriber)
  if (response?.status !== 'completed') throw new Error(response?.error?.message || '未取得权限执行状态，请稍后手动刷新。')
  const result = readAuthorizationBootstrap(response.authorizationBootstrap)
  if (!result || result.operationId !== operationId) throw new Error('权限状态响应与原操作不符。')
  return result
}

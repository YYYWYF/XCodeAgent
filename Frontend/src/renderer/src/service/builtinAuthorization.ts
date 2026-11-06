import { randomUUID, type AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { readAuthorizationBootstrap, type WorkflowAuthorizationBootstrap } from './authorizationBootstrap'

export type BuiltinPatchStage = 'schema' | 'write' | 'verify' | 'query'
export type BuiltinPatchAction = 'get' | 'run_patch' | 'refresh_patch'
export type BuiltinPatchResult = {
  stage: BuiltinPatchStage
  status: 'waiting' | 'ready' | 'continue' | 'failed' | 'unknown'
  message: string
  schemaReady: boolean
  schemaOperationId?: string
  seedOperationId?: string
  nextAction?: 'refresh' | 'run'
  createdCount?: number
  existingCount?: number
  resourceCount?: number
  roleGrantCount?: number
}

type AuthorizationResponse = {
  status?: string
  authorizationBootstrap?: unknown
  patch?: unknown
  progress?: { stage?: string; message?: string }
  error?: { type?: string; message?: string }
}

/** 标识服务端已返回的失败，与网络断开导致的未知结果分开显示。 */
export class BuiltinPatchResponseError extends Error {}

/** 使用独立 AG-UI 动作查询原操作一次，不轮询、不恢复 Build 或提交建表。 */
export async function refreshBuiltinAuthorization(
  workspaceRoot: string,
  operationId: string,
  operationKind: 'schema' | 'seed' = 'schema'
): Promise<WorkflowAuthorizationBootstrap> {
  const response = await runAuthorizationAction({
    action: 'refresh', workspaceRoot, operationId, ...(operationKind === 'seed' ? { operationKind } : {})
  }, '刷新原权限初始化操作的执行状态。')
  const result = readAuthorizationBootstrap(response.authorizationBootstrap)
  if (!result || (operationKind === 'seed' ? result.seedOperationId : result.operationId) !== operationId) throw new Error('权限状态响应与原操作不符。')
  return result
}

/** 在独立 AG-UI 线程读取、执行或查询补丁，并接收当前阶段。 */
export async function runBuiltinPatch(
  workspaceRoot: string,
  action: BuiltinPatchAction,
  onProgress?: (stage: BuiltinPatchStage, message: string) => void
): Promise<BuiltinPatchResult | null> {
  const response = await runAuthorizationAction({ action, workspaceRoot }, '检查内置数据库权限补丁。', onProgress)
  if (action === 'get' && response.patch === null) return null
  const patch = response.patch as Partial<BuiltinPatchResult> | undefined
  if (!patch || !['schema', 'write', 'verify'].includes(String(patch.stage)) ||
      !['waiting', 'ready', 'continue', 'failed', 'unknown'].includes(String(patch.status)) ||
      typeof patch.message !== 'string' || typeof patch.schemaReady !== 'boolean') {
    throw new Error('未取得有效的权限补丁状态。')
  }
  return patch as BuiltinPatchResult
}

/** 通过 AG-UI SDK 接收阶段、终态和安全错误信息。 */
async function runAuthorizationAction(
  action: Record<string, string>,
  prompt: string,
  onProgress?: (stage: BuiltinPatchStage, message: string) => void
): Promise<AuthorizationResponse> {
  const base = window.devAgentStudio?.agentBaseUrl
  const agent = createAgUiHttpAgent({
    url: base ? `${base.replace(/\/$/, '')}/builtin-authorization/run` : '/api/agent/builtin-authorization/run',
    threadId: randomUUID()
  })
  let response: AuthorizationResponse | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'builtin-authorization') return
      response = event.value
      const progress = response?.progress
      if (response?.status === 'in_progress' && progress?.message &&
          ['schema', 'write', 'verify', 'query'].includes(String(progress.stage))) {
        onProgress?.(progress.stage as BuiltinPatchStage, progress.message)
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      response = event.snapshot.builtinAuthorization ?? response
    }
  }
  agent.addMessage({ id: randomUUID(), role: 'user', content: prompt })
  await agent.runAgent({ forwardedProps: { builtinAuthorization: action } }, subscriber)
  if (response?.status === 'failed') {
    const error = action.action !== 'refresh' && response.error?.type === 'ValidationError'
      ? '当前后端不支持该权限补丁动作，请核对前后端运行版本。' : response.error?.message
    throw new BuiltinPatchResponseError(error || '权限操作失败。')
  }
  if (response?.status !== 'completed') throw new Error('未取得权限执行状态，请稍后手动刷新。')
  return response
}

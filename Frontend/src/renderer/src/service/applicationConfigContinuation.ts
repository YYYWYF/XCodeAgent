import type { WorkflowRunPayload } from '../typings'
import {
  applyConfigChanges,
  checkPendingConfigOperation,
  clearConfigContinuation,
  inspectPendingConfigIdentity,
  readConfigContinuation,
  resumePendingConfigOperation,
  type ConfigChanges,
  type ConfigOperation
} from './applicationConfigOperation'

export type ConfigPrerequisiteAction =
  | { action: 'apply'; changes: ConfigChanges }
  | { action: 'resume' | 'continue' | 'cancelled' }

export type ConfigPrerequisiteMode = 'apply' | 'resume' | 'continue'
type ConfigActionPhase = 'configuring' | 'continuing'

const inFlight = new Map<string, { action: ConfigPrerequisiteAction['action']; phase: ConfigActionPhase; promise: Promise<boolean> }>()
const listeners = new Set<() => void>()

/** 同一工作区的同一配置请求共享提交锁和进度。 */
function requestKey(workspaceRoot: string, requestId: string): string {
  return JSON.stringify([workspaceRoot, requestId])
}

/** 让重挂载后的配置卡订阅仍在执行的原动作。 */
export function subscribeConfigAction(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 读取配置同步或会话续跑的实时阶段。 */
export function configActionPhase(workspaceRoot: string, requestId: string): ConfigActionPhase | undefined {
  return inFlight.get(requestKey(workspaceRoot, requestId))?.phase
}

/** 用现有 AG-UI 查询核实本地操作，区分新提案、原操作和仅续跑。 */
export async function resolveConfigPrerequisiteMode(workspaceRoot: string, requestId: string): Promise<ConfigPrerequisiteMode> {
  if (!requestId.trim()) throw new Error('应用配置确认缺少原请求身份。')
  if (readConfigContinuation(workspaceRoot, requestId)) return 'continue'
  const pending = inspectPendingConfigIdentity(workspaceRoot, requestId)
  if (pending.kind === 'invalid') throw new Error('无法确认上次配置的身份，已保留原记录。')
  if (pending.kind === 'other') {
    throw new Error(pending.requestId
      ? '此应用存在另一会话尚未完成的配置操作，请回到原会话处理。'
      : '此应用存在设置页尚未完成的配置操作，请到应用设置继续处理。')
  }
  if (pending.kind === 'none') return 'apply'
  const operation = await checkPendingConfigOperation(workspaceRoot, requestId, pending.operationId)
  return operation.status === 'APPLIED' ? 'continue' : 'resume'
}

/** 只采信原线程、原请求的配置确认 checkpoint。 */
export function configCheckpointStatus(
  workflow: WorkflowRunPayload | undefined,
  threadId: string,
  requestId: string
): unknown {
  const pending = workflow?.state?.application_config_prerequisite as Record<string, unknown> | undefined
  if (workflow?.threadId === threadId && pending?.sourceThreadId === threadId && pending.requestId === requestId) {
    return pending.status
  }
  return undefined
}

/** 区分配置完成、会话接收及显式取消三种后端事实。 */
export function configDecisionAccepted(status: unknown, decision: 'applied' | 'cancelled'): boolean {
  return decision === 'cancelled' ? status === 'CANCELLED' : status === 'CONFIG_APPLIED' || status === 'RESUMED'
}

/** 冷进入时用已推进的 checkpoint 收口先前断开的续跑。 */
export function settleConfigContinuation(workspaceRoot: string, threadId: string, workflow?: WorkflowRunPayload):
  { requestId: string; result: 'confirmed' | 'cancelled' } | undefined {
  const pending = workflow?.state?.application_config_prerequisite as { requestId?: unknown } | undefined
  const requestId = pending?.requestId
  if (typeof requestId !== 'string') return undefined
  const status = configCheckpointStatus(workflow, threadId, requestId)
  const result = configDecisionAccepted(status, 'applied') ? 'confirmed' : status === 'CANCELLED' ? 'cancelled' : undefined
  if (!result) return undefined
  // 本地读写失败不影响服务端已接收的事实，也保留损坏记录供核对。
  try {
    const operationId = readConfigContinuation(workspaceRoot, requestId)
    if (operationId) clearConfigContinuation(workspaceRoot, requestId, operationId)
  } catch { /* 保留本地记录。 */ }
  return { requestId, result }
}

/** 串联配置和原会话；已落盘的续跑身份只能用于继续会话。 */
export function runConfigPrerequisiteAction(
  workspaceRoot: string,
  requestId: string,
  expectedConfigRevision: number,
  input: ConfigPrerequisiteAction,
  continueRequest: (operationId?: string, operation?: ConfigOperation) => Promise<boolean>
): Promise<boolean> {
  const key = requestKey(workspaceRoot, requestId)
  const current = inFlight.get(key)
  if (current) {
    return current.action === input.action ? current.promise : Promise.reject(new Error('当前配置请求正在处理。'))
  }

  const entry = {
    action: input.action,
    phase: (input.action === 'apply' || input.action === 'resume' ? 'configuring' : 'continuing') as ConfigActionPhase,
    promise: Promise.resolve().then(async () => {
      const saved = readConfigContinuation(workspaceRoot, requestId)
      if (input.action === 'cancelled') {
        const accepted = await continueRequest()
        if (accepted && saved) clearConfigContinuation(workspaceRoot, requestId, saved)
        return accepted
      }
      let operation: ConfigOperation | undefined
      let operationId = saved
      if (!operationId && input.action !== 'continue') {
        const pending = inspectPendingConfigIdentity(workspaceRoot, requestId)
        if (input.action === 'apply' && pending.kind === 'none') {
          operation = await applyConfigChanges(workspaceRoot, expectedConfigRevision, input.changes, undefined, requestId)
        } else if (input.action === 'resume' && pending.kind === 'mine') {
          operation = await resumePendingConfigOperation(workspaceRoot, requestId)
        } else {
          throw new Error('原配置操作身份已变化，请检查当前配置卡。')
        }
        operationId = readConfigContinuation(workspaceRoot, requestId)
        if (operation.status !== 'APPLIED' || !operationId || operation.operationId !== operationId) {
          throw new Error('工程配置尚未完成同步，请检查后重试。')
        }
      }
      if (!operationId) throw new Error('找不到当前请求已完成的配置操作。')
      entry.phase = 'continuing'
      listeners.forEach((listener) => listener())
      const accepted = await continueRequest(operationId, operation)
      if (accepted) clearConfigContinuation(workspaceRoot, requestId, operationId)
      return accepted
    }).finally(() => {
      inFlight.delete(key)
      listeners.forEach((listener) => listener())
    })
  }
  inFlight.set(key, entry)
  listeners.forEach((listener) => listener())
  return entry.promise
}

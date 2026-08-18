import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

// AG-UI agent 动作的 generic 基座：把各 service 模块重复的「payload 信封校验 +
// customEvent/stateSnapshot/result 三路合并 + failed 抛错」脚手架集中在此。
// 各 service 模块（userSkills/agentFiles/applicationDevelopmentPlanning/codeChanges 等）
// 自身只声明业务 payload 类型与字段读取，调用 runAgUiAction 即可。

/** AG-UI 自定义事件 / state snapshot / final result 共用的 payload 信封。业务 payload 须满足此约束。 */
export type AgUiPayloadEnvelope = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: string
  error?: { message?: string }
}

/** 校验 AG-UI payload 信封字段（schemaVersion/runId/threadId/status）。业务字段由泛型 T 承载。 */
export function readAgUiPayload<T extends AgUiPayloadEnvelope>(
  value: unknown,
  statusList: readonly string[]
): T | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<T>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !statusList.includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as T
}

/** 从 AG-UI STATE_SNAPSHOT 的指定 key 读取 payload。 */
export function readAgUiPayloadFromSnapshot<T extends AgUiPayloadEnvelope>(
  snapshot: unknown,
  key: string,
  statusList: readonly string[]
): T | undefined {
  if (!snapshot || typeof snapshot !== 'object') return undefined
  return readAgUiPayload<T>((snapshot as Record<string, unknown>)[key], statusList)
}

/** 从 AG-UI 最终结果的指定 key 读取 payload。 */
export function readAgUiPayloadFromResult<T extends AgUiPayloadEnvelope>(
  result: unknown,
  key: string,
  statusList: readonly string[]
): T | undefined {
  if (!result || typeof result !== 'object') return undefined
  return readAgUiPayload<T>((result as Record<string, unknown>)[key], statusList)
}

export type RunAgUiActionOpts<TPayload extends AgUiPayloadEnvelope> = {
  /** AG-UI agent HTTP 地址。 */
  url: string
  /** 用户消息内容。 */
  message: string
  /** customEvent name（流中载荷事件名）。 */
  eventName: string
  /** state snapshot / final result 中的 payload key。 */
  stateKey: string
  /** 完整的 forwardedProps 对象（由调用方组装，含业务字段）。 */
  forwardedProps: Record<string, unknown>
  /** 合法 status 枚举（用于 payload 校验）。 */
  statusList: readonly string[]
  /** 可选：外部传入 threadId，否则内部 randomUUID。 */
  threadId?: string
  /** 可选：customEvent 收到新 payload 时回调（如 progress 推进；仅在 customEvent 触发，不随 snapshot/result 重复）。 */
  onCustomEventPayload?: (payload: TPayload) => void
  /** 可选：流式文本 delta（模型流式输出）。 */
  onTextDelta?: (delta: string) => void
  /** 可选：无 payload 时的错误文案。 */
  emptyMessage?: string
  /** 可选：status=failed 时的兜底错误文案。 */
  failedMessage?: string
}

/**
 * 运行一次 AG-UI agent 动作：统一合并 customEvent / stateSnapshot / final result 三路 payload，
 * 校验信封字段，status=failed 时抛错。各 service 模块用此 generic，自身只声明业务 payload 类型与字段读取。
 */
export async function runAgUiAction<TPayload extends AgUiPayloadEnvelope>(
  opts: RunAgUiActionOpts<TPayload>
): Promise<TPayload> {
  const threadId = opts.threadId ?? randomUUID()
  const agent = createAgUiHttpAgent({ url: opts.url, threadId })
  const userMessage: Message = { id: randomUUID(), role: 'user', content: opts.message }
  agent.addMessage(userMessage)

  let payload: TPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === opts.eventName) {
        const next = readAgUiPayload<TPayload>(event.value, opts.statusList)
        if (next) {
          payload = next
          opts.onCustomEventPayload?.(next)
        }
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readAgUiPayloadFromSnapshot<TPayload>(event.snapshot, opts.stateKey, opts.statusList) ?? payload
    },
    ...(opts.onTextDelta
      ? {
          onTextMessageContentEvent: ({ event, textMessageBuffer }) =>
            opts.onTextDelta?.(`${textMessageBuffer}${event.delta}`),
          onTextMessageEndEvent: ({ textMessageBuffer }) => opts.onTextDelta?.(textMessageBuffer)
        }
      : {})
  }
  const result = await agent.runAgent({ forwardedProps: opts.forwardedProps }, subscriber)
  payload = readAgUiPayloadFromResult<TPayload>(result.result, opts.stateKey, opts.statusList) ?? payload

  if (!payload) throw new Error(opts.emptyMessage ?? '接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || opts.failedMessage || '操作失败。')
  }
  return payload
}

import { HttpAgent, type AgentSubscriber } from '@ag-ui/client'
import type { Context, ResumeEntry } from '@ag-ui/core'
import { decodeRuntimeReceipt, type RuntimeBusinessResult } from './runtimeResults'

interface RuntimeRequest {
  endpoint: string
  agentId: string
  threadId: string
  context: readonly Context[]
  response?: ResumeEntry
}

/** 显式只读查询或恢复已有待办；不追加用户消息、不重新提交原业务操作。 */
export function requestRuntimeState(request: RuntimeRequest):
  { result: Promise<RuntimeBusinessResult>; abort: () => void } {
  const client = new HttpAgent({ url: request.endpoint, agentId: request.agentId,
    threadId: request.threadId, initialMessages: [], initialState: {} })
  const runId = crypto.randomUUID()
  let started = false
  let finished = false
  let receipt: RuntimeBusinessResult | null = null
  let failure = ''
  const subscriber: AgentSubscriber = {
    onRunStartedEvent: ({ event }) => { started = event.runId === runId && event.threadId === request.threadId },
    onCustomEvent: ({ event }) => {
      const value = decodeRuntimeReceipt(event.name, event.value)
      if (value?.agentId === request.agentId && value.threadId === request.threadId && value.runId === runId) receipt = value
    },
    onRunFinishedEvent: ({ event }) => { finished = event.runId === runId && event.threadId === request.threadId },
    onRunErrorEvent: ({ event }) => { failure = event.message },
  }
  /** SDK 负责传输和事件解析；缺完整回执时不猜原业务结果。 */
  const result = client.runAgent({ runId, context: [...request.context],
    ...(request.response ? { resume: [request.response] } : { forwardedProps: { inspectInteraction: true } }),
  }, subscriber).then((): RuntimeBusinessResult => {
    if (!started || !finished || !receipt || failure) throw new Error(failure || '未收到完整状态，请再次查询。')
    return receipt
  })
  return { result, abort: (): void => client.abortRun() }
}

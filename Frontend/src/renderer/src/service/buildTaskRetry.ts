import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type BuildTaskRetryRequest = {
  action: 'enqueue' | 'retry'
  workspaceRoot: string
  sourceRunId: string
  sourceThreadId: string
  sessionId: string
  taskId: string
}

/** 返回独立任务重试地址，和整节点恢复入口保持分离。 */
export function getBuildTaskRetryUrl(): string {
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  return origin ? `${origin}/build-task-retry/run` : '/api/agent/build-task-retry/run'
}

/** 用独立 AG-UI 请求入队，原 Build 的流继续负责执行进度。 */
export async function enqueueBuildTaskRetry(request: BuildTaskRetryRequest): Promise<void> {
  const agent = createAgUiHttpAgent({ url: getBuildTaskRetryUrl(), threadId: randomUUID() })
  let completed = false
  let failure = ''
  await agent.runAgent({ runId: randomUUID(), forwardedProps: { buildTaskRetry: request } }, {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'build-task-retry') return
      if (event.value?.status === 'failed') failure = event.value.error?.message || '任务重试失败。'
      if (event.value?.status === 'completed') completed = true
    }
  })
  if (failure || !completed) throw new Error(failure || '未收到任务入队回执，请刷新状态。')
}

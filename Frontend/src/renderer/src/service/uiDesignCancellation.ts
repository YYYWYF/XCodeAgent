import { randomUUID, type HttpAgent } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

type StopRequest = { workspaceRoot: string; pageId: string; generationId: string }

/** 经官方 AG-UI SDK 等待停止终态，收到完成生命周期也必须核对业务成功与任务代次。 */
export async function cancelUiDesignGeneration(
  request: StopRequest,
  transport?: Pick<HttpAgent, 'runAgent'>
): Promise<void> {
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = transport || createAgUiHttpAgent({
    url: origin ? `${origin}/ui-design-generation/run` : '/api/agent/ui-design-generation/run',
    threadId: `ui-design-stop-${randomUUID()}`
  })
  let stopped = false
  let failure = ''
  /** 只消费本次动作结果，进度和不匹配代次不能冒充停止成功。 */
  const accept = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    const result = value as { status?: string; pageId?: string; generationId?: string; cancelled?: boolean; error?: { message?: string } }
    if (result.status === 'failed') failure = result.error?.message || '停止设计稿生成失败。'
    if (result.status === 'completed' && result.pageId === request.pageId &&
        result.generationId === request.generationId && result.cancelled === true) stopped = true
  }
  const result = await agent.runAgent({ forwardedProps: { uiDesignGeneration: { action: 'cancel', ...request } } }, {
    onCustomEvent: ({ event }) => { if (event.name === 'ui-design-generation') accept(event.value) },
    onStateSnapshotEvent: ({ event }) => accept((event.snapshot as { uiDesignGeneration?: unknown }).uiDesignGeneration)
  })
  accept((result.result as { uiDesignGeneration?: unknown } | undefined)?.uiDesignGeneration)
  if (failure) throw new Error(failure)
  if (!stopped) throw new Error('尚未收到后台退出回执，请同步后重试停止。')
}

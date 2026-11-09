import { randomUUID, type HttpAgent } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

type PageRequest = { workspaceRoot: string; pageId: string }
type StopRequest = PageRequest & { stopToken: string }
export type UiDesignGenerationState = {
  pageId: string
  stopToken: string
  taskState: 'active' | 'preparing' | 'orphaned' | 'settled'
  pageStatus: string
  settled?: boolean
}

/** 经官方 AG-UI SDK 等待停止终态，收到完成生命周期也必须核对业务成功与任务代次。 */
async function runGenerationAction(
  request: PageRequest & { action: 'get' | 'cancel'; stopToken?: string },
  transport?: Pick<HttpAgent, 'runAgent'>
): Promise<UiDesignGenerationState> {
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = transport || createAgUiHttpAgent({
    url: origin ? `${origin}/ui-design-generation/run` : '/api/agent/ui-design-generation/run',
    threadId: `ui-design-stop-${randomUUID()}`
  })
  let snapshot: UiDesignGenerationState | undefined
  let failure = ''
  /** 只消费本次动作结果，进度和不匹配代次不能冒充停止成功。 */
  const accept = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    const result = value as Partial<UiDesignGenerationState> & { status?: string; error?: { message?: string } }
    if (result.status === 'failed') failure = result.error?.message || '停止设计稿生成失败。'
    if (result.status === 'completed' && result.pageId === request.pageId && result.stopToken &&
        (request.action === 'get' || (result.stopToken === request.stopToken && result.settled === true))) {
      snapshot = result as UiDesignGenerationState
    }
  }
  const result = await agent.runAgent({ forwardedProps: { uiDesignGeneration: request } }, {
    onCustomEvent: ({ event }) => { if (event.name === 'ui-design-generation') accept(event.value) },
    onStateSnapshotEvent: ({ event }) => accept((event.snapshot as { uiDesignGeneration?: unknown }).uiDesignGeneration)
  })
  accept((result.result as { uiDesignGeneration?: unknown } | undefined)?.uiDesignGeneration)
  if (failure) throw new Error(failure)
  if (!snapshot) throw new Error('尚未收到有效的后台任务回执，请同步后重试。')
  return snapshot
}

/** 通过后台读取真实任务及停止凭据，查询不触发生成或清单写入。 */
export function getUiDesignGeneration(request: PageRequest, transport?: Pick<HttpAgent, 'runAgent'>): Promise<UiDesignGenerationState> {
  return runGenerationAction({ action: 'get', ...request }, transport)
}

/** 只停止后台签发凭据绑定的任务或残留状态，禁止旧请求操作新代次。 */
export async function cancelUiDesignGeneration(request: StopRequest, transport?: Pick<HttpAgent, 'runAgent'>): Promise<void> {
  await runGenerationAction({ action: 'cancel', ...request }, transport)
}

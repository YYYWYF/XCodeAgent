import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

export type AgentSurfaceSelectionDraft = {
  productPlan: Record<string, unknown>
  productPlanHash: string
}

type SelectionRequest =
  | { action: 'get'; workspaceRoot: string }
  | {
      action: 'save'
      workspaceRoot: string
      agentId: string
      pageId: string
      enabled: boolean
      basedOnProductPlanHash: string
    }

type SelectionResult = {
  status?: string
  error?: { message?: string }
  productPlan?: unknown
  productPlanHash?: unknown
}

/** 通过原 AG-UI 端点读取同一版本的当前草稿及服务端摘要。 */
export function getAgentSurfaceSelectionDraft(
  workspaceRoot: string
): Promise<AgentSurfaceSelectionDraft> {
  return runAgentSurfaceSelection({ action: 'get', workspaceRoot })
}

/** 用当前已展示的服务端摘要保存浮窗选择，并返回下一次编辑基线。 */
export function saveAgentSurfaceSelection(
  workspaceRoot: string,
  agentId: string,
  pageId: string,
  enabled: boolean,
  basedOnProductPlanHash: string
): Promise<AgentSurfaceSelectionDraft> {
  return runAgentSurfaceSelection({
    action: 'save',
    workspaceRoot,
    agentId,
    pageId,
    enabled,
    basedOnProductPlanHash
  })
}

/** 消费标准客户端的动作结果，读取和保存共用完整生命周期及错误语义。 */
async function runAgentSurfaceSelection(
  input: SelectionRequest
): Promise<AgentSurfaceSelectionDraft> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: randomUUID() })
  agent.addMessage({
    id: randomUUID(),
    role: 'user',
    content:
      input.action === 'get' ? '读取当前智能体浮窗选择草稿。' : '保存当前页面的智能体浮窗选择。'
  })
  let result: SelectionResult | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (
        event.name === 'agent-surface-selection-draft' &&
        event.value &&
        typeof event.value === 'object'
      )
        result = event.value
    }
  }
  await agent.runAgent(
    {
      forwardedProps: {
        agentSurfaceSelection: input
      }
    },
    subscriber
  )
  if (result?.status !== 'completed')
    throw new Error(result?.error?.message || '智能体浮窗草稿操作失败')
  if (
    !result.productPlan ||
    typeof result.productPlan !== 'object' ||
    Array.isArray(result.productPlan) ||
    typeof result.productPlanHash !== 'string' ||
    !/^sha256:[0-9a-f]{64}$/.test(result.productPlanHash)
  ) {
    throw new Error('智能体浮窗操作未返回有效的当前草稿及版本摘要。')
  }
  return {
    productPlan: result.productPlan as Record<string, unknown>,
    productPlanHash: result.productPlanHash
  }
}

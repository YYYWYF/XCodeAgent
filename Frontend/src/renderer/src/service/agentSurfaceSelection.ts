import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

/** 只保存待确认产品草稿的浮窗选择，正式联合确认仍沿原规划流程。 */
export async function saveAgentSurfaceSelection(
  workspaceRoot: string,
  agentId: string,
  pageId: string,
  enabled: boolean,
  productPlan: Record<string, unknown>
): Promise<void> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: randomUUID() })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '保存当前页面的智能体浮窗选择。' })
  let result: { status?: string; error?: { message?: string } } | undefined
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
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(JSON.stringify(canonicalValue(productPlan)))
  )
  const basedOnProductPlanHash = `sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')}`
  await agent.runAgent(
    {
      forwardedProps: {
        agentSurfaceSelection: {
          action: 'save',
          workspaceRoot,
          agentId,
          pageId,
          enabled,
          basedOnProductPlanHash
        }
      }
    },
    subscriber
  )
  if (result?.status !== 'completed')
    throw new Error(result?.error?.message || '智能体浮窗选择保存失败')
}

/** 保留数组顺序并递归排序 JSON 对象键，与服务端草稿摘要使用同一规范。 */
function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue)
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([key, item]) => [key, canonicalValue(item)])
    )
  return value
}

import { randomUUID, type AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

export type RequirementAuthorizationPreview = {
  enabled: boolean
  ready: boolean
  manifest?: Record<string, unknown>
}

/** 通过独立 AG-UI 只读预览当前需求草稿，配置开关始终由后端读取工作区。 */
export async function readRequirementAuthorization(
  workspaceRoot: string, requirementSpec: Record<string, unknown>,
  productPlan: Record<string, unknown>, signal: AbortSignal
): Promise<RequirementAuthorizationPreview> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: randomUUID() })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '读取当前需求文档的角色与权限资源。' })
  let result: Record<string, unknown> | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'requirement-authorization' && event.value && typeof event.value === 'object')
        result = event.value as Record<string, unknown>
    }
  }
  /** 关闭视图只取消本次读取，不影响原规划执行或确认门。 */
  const abort = (): void => agent.abortRun()
  signal.addEventListener('abort', abort, { once: true })
  try {
    if (signal.aborted) throw new Error('角色与权限读取已取消。')
    await agent.runAgent({ forwardedProps: { requirementAuthorization: {
      action: 'preview', workspaceRoot, requirementSpec, productPlan
    } } }, subscriber)
    if (signal.aborted) throw new Error('角色与权限读取已取消。')
    if (result?.status === 'failed')
      throw new Error((result.error as { message?: string } | undefined)?.message || '角色与权限读取失败。')
    if (result?.status !== 'completed' || typeof result.enabled !== 'boolean' || typeof result.ready !== 'boolean' ||
      (result.ready && (!result.manifest || typeof result.manifest !== 'object' || Array.isArray(result.manifest))))
      throw new Error('角色与权限没有返回有效的资源视图。')
    return result as unknown as RequirementAuthorizationPreview
  } finally {
    signal.removeEventListener('abort', abort)
  }
}

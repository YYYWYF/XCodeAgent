import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

type DeletionResult = {
  schemaVersion?: number
  status?: string
  action?: string
  applicationId?: string
  workspaceRoot?: string
  deletionCompleted?: boolean
  error?: { message?: string }
}

/** 通过独立 AG-UI 动作确认目录已移入回收站，解除旧路径的后端运行封锁。 */
export async function completeApplicationDeletion(
  applicationId: string,
  workspaceRoot: string
): Promise<void> {
  const baseUrl = window.xcodeAgent?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  const agent = createAgUiHttpAgent({
    url: `${baseUrl}/application-deletion/run`,
    threadId: randomUUID()
  })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '确认应用目录已移入回收站。' })
  let result: DeletionResult | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'application-deletion') result = event.value as DeletionResult
    },
    onStateSnapshotEvent: ({ event }) => {
      const snapshot = event.snapshot as { applicationDeletion?: DeletionResult }
      result = snapshot.applicationDeletion ?? result
    }
  }
  await agent.runAgent(
    {
      forwardedProps: { applicationDeletion: { action: 'complete', applicationId, workspaceRoot } }
    },
    subscriber
  )
  if (result?.status === 'failed') {
    throw new Error(result.error?.message || '应用删除收尾失败，请重试删除。')
  }
  if (
    result?.schemaVersion !== 1 ||
    result?.status !== 'completed' ||
    result.action !== 'complete' ||
    result.applicationId !== applicationId ||
    result.workspaceRoot !== workspaceRoot ||
    result.deletionCompleted !== true
  ) {
    throw new Error('后端未确认应用删除完成，请重试删除。')
  }
}

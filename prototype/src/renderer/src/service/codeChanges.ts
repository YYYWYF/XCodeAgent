import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { WorkspaceCodeChangeSet } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type CodeChangesActionPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'revert'
  changeSetId?: string
  workspaceRoot?: string
  revertedPaths?: string[]
  revertedAt?: string
  error?: { type?: string; message?: string }
}

export type RevertedCodeChanges = {
  changeSetId: string
  revertedPaths: string[]
  revertedAt: string
}

/** 返回代码变更 AG-UI 操作地址。 */
function getCodeChangesUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/code-changes/run`
    : '/api/agent/code-changes/run'
}

/** 从未知值中读取已完成的代码变更操作状态。 */
function readCodeChangesAction(value: unknown): CodeChangesActionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<CodeChangesActionPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as CodeChangesActionPayload
}

/** 从 AG-UI 状态快照中读取代码变更操作。 */
function readCodeChangesActionFromState(value: unknown): CodeChangesActionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readCodeChangesAction((value as { codeChangesAction?: unknown }).codeChangesAction)
}

/** 从 AG-UI 最终结果中读取代码变更操作。 */
function readCodeChangesActionFromResult(value: unknown): CodeChangesActionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readCodeChangesAction((value as { codeChangesAction?: unknown }).codeChangesAction)
}

/** 通过独立 AG-UI 流精确撤销指定历史代码变更集。 */
export async function revertWorkspaceCodeChanges(
  codeChanges: WorkspaceCodeChangeSet
): Promise<RevertedCodeChanges> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getCodeChangesUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: `撤销代码变更集 ${codeChanges.id}。`
  }
  agent.addMessage(message)

  let actionResult: CodeChangesActionPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'code-changes') {
        actionResult = readCodeChangesAction(event.value) ?? actionResult
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      actionResult = readCodeChangesActionFromState(event.snapshot) ?? actionResult
    }
  }
  const result = await agent.runAgent(
    {
      forwardedProps: {
        codeChangesAction: {
          action: 'revert',
          confirmed: true,
          workspaceRoot: codeChanges.workspaceRoot,
          changeSet: codeChanges
        }
      }
    },
    subscriber
  )
  actionResult = readCodeChangesActionFromResult(result.result) ?? actionResult
  if (!actionResult) throw new Error('撤销接口没有返回有效的 AG-UI 状态。')
  if (actionResult.status === 'failed') {
    throw new Error(actionResult.error?.message || '撤销代码变更失败。')
  }
  if (
    actionResult.changeSetId !== codeChanges.id ||
    !Array.isArray(actionResult.revertedPaths) ||
    typeof actionResult.revertedAt !== 'string'
  ) {
    throw new Error('撤销接口没有返回完整结果。')
  }
  return {
    changeSetId: actionResult.changeSetId,
    revertedPaths: actionResult.revertedPaths,
    revertedAt: actionResult.revertedAt
  }
}

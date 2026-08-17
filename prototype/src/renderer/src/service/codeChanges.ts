import type { WorkspaceCodeChangeSet } from '../typings'
import { runAgUiAction, type AgUiPayloadEnvelope } from './agUiClient'

type CodeChangesActionPayload = AgUiPayloadEnvelope & {
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

const CODE_CHANGES_STATUS_LIST = ['completed', 'failed'] as const
const CODE_CHANGES_EVENT = 'code-changes'
const CODE_CHANGES_ACTION_KEY = 'codeChangesAction'

/** 返回代码变更 AG-UI 操作地址。 */
function getCodeChangesUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/code-changes/run`
    : '/api/agent/code-changes/run'
}

/** 通过独立 AG-UI 流精确撤销指定历史代码变更集。payload 合并/校验/失败抛错由 runAgUiAction 处理。 */
export async function revertWorkspaceCodeChanges(
  codeChanges: WorkspaceCodeChangeSet
): Promise<RevertedCodeChanges> {
  const actionResult = await runAgUiAction<CodeChangesActionPayload>({
    url: getCodeChangesUrl(),
    message: `撤销代码变更集 ${codeChanges.id}。`,
    eventName: CODE_CHANGES_EVENT,
    stateKey: CODE_CHANGES_ACTION_KEY,
    forwardedProps: {
      [CODE_CHANGES_ACTION_KEY]: {
        action: 'revert',
        confirmed: true,
        workspaceRoot: codeChanges.workspaceRoot,
        changeSet: codeChanges
      }
    },
    statusList: CODE_CHANGES_STATUS_LIST,
    emptyMessage: '撤销接口没有返回有效的 AG-UI 状态。',
    failedMessage: '撤销代码变更失败。'
  })
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

import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { VersionControlCommitResult, VersionControlSnapshot } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type VersionControlAction = 'inspect' | 'commit'

type VersionControlAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: VersionControlAction
  snapshot?: VersionControlSnapshot
  workspaceRoot?: string
  repositoryRoot?: string
  commitSha?: string
  message?: string
  committedPaths?: string[]
  remainingDirty?: boolean
  error?: { type?: string; message?: string }
}

/** 返回独立版本控制 AG-UI 动作地址。 */
function getVersionControlUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/version-control/run`
    : '/api/agent/version-control/run'
}

/** 校验 AG-UI 自定义事件或状态快照中的版本控制载荷。 */
function readVersionControlPayload(value: unknown): VersionControlAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<VersionControlAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as VersionControlAgUiPayload
}

/** 从 AG-UI 状态快照中提取版本控制业务结果。 */
function readVersionControlFromState(value: unknown): VersionControlAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readVersionControlPayload((value as { versionControl?: unknown }).versionControl)
}

/** 从 AG-UI 最终结果中提取版本控制业务结果。 */
function readVersionControlFromResult(value: unknown): VersionControlAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readVersionControlPayload((value as { versionControl?: unknown }).versionControl)
}

/** 校验后端返回的 Git 状态快照是否完整可用。 */
function isVersionControlSnapshot(value: unknown): value is VersionControlSnapshot {
  if (!value || typeof value !== 'object') return false
  const snapshot = value as Partial<VersionControlSnapshot>
  return (
    typeof snapshot.workspaceRoot === 'string' &&
    typeof snapshot.repositoryRoot === 'string' &&
    typeof snapshot.branch === 'string' &&
    typeof snapshot.head === 'string' &&
    typeof snapshot.fingerprint === 'string' &&
    /^[a-f0-9]{64}$/.test(snapshot.fingerprint) &&
    typeof snapshot.dirty === 'boolean' &&
    typeof snapshot.hasStagedChanges === 'boolean' &&
    Array.isArray(snapshot.files) &&
    Array.isArray(snapshot.requestedPaths) &&
    Array.isArray(snapshot.eligiblePaths) &&
    Array.isArray(snapshot.unavailablePaths)
  )
}

/** 发送一次标准 AG-UI 版本控制动作并统一收敛结果。 */
async function runVersionControl(
  input: Record<string, unknown>,
  messageContent: string
): Promise<VersionControlAgUiPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getVersionControlUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: messageContent
  }
  agent.addMessage(message)

  let versionControl: VersionControlAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'version-control') {
        versionControl = readVersionControlPayload(event.value) ?? versionControl
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      versionControl = readVersionControlFromState(event.snapshot) ?? versionControl
    }
  }
  const result = await agent.runAgent({ forwardedProps: { versionControl: input } }, subscriber)
  versionControl = readVersionControlFromResult(result.result) ?? versionControl
  if (!versionControl) throw new Error('版本控制接口没有返回有效的 AG-UI 状态。')
  if (versionControl.status === 'failed') {
    throw new Error(versionControl.error?.message || '版本控制操作失败。')
  }
  return versionControl
}

/** 重新读取当前 Git 状态并返回本轮变更的可提交文件。 */
export async function inspectVersionControl(input: {
  workspaceRoot: string
  requestedPaths: string[]
}): Promise<VersionControlSnapshot> {
  const response = await runVersionControl(
    { action: 'inspect', ...input },
    '重新检查本次快速修改的 Git 状态。'
  )
  if (!isVersionControlSnapshot(response.snapshot)) {
    throw new Error('版本控制接口没有返回完整的 Git 状态。')
  }
  return response.snapshot
}

/** 在用户确认后提交精确选择的二次修改文件。 */
export async function commitVersionControl(input: {
  workspaceRoot: string
  requestedPaths: string[]
  selectedPaths: string[]
  expectedFingerprint: string
  message: string
}): Promise<VersionControlCommitResult> {
  const response = await runVersionControl(
    { action: 'commit', confirmed: true, ...input },
    '提交已审阅的快速修改文件。'
  )
  if (
    response.action !== 'commit' ||
    typeof response.workspaceRoot !== 'string' ||
    typeof response.repositoryRoot !== 'string' ||
    typeof response.commitSha !== 'string' ||
    typeof response.message !== 'string' ||
    !Array.isArray(response.committedPaths) ||
    typeof response.remainingDirty !== 'boolean' ||
    !isVersionControlSnapshot(response.snapshot)
  ) {
    throw new Error('版本控制接口没有返回完整的提交结果。')
  }
  return {
    action: 'commit',
    workspaceRoot: response.workspaceRoot,
    repositoryRoot: response.repositoryRoot,
    commitSha: response.commitSha,
    message: response.message,
    committedPaths: response.committedPaths,
    remainingDirty: response.remainingDirty,
    snapshot: response.snapshot
  }
}

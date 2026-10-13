import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { VersionControlCommitResult, VersionControlSnapshot } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type VersionControlAction = 'inspect' | 'inspect_all' | 'commit' | 'summarize'

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
  /** summarize 动作生成的业务变更说明；生成不出来时是空串。 */
  changeSummary?: string
  error?: { type?: string; message?: string }
}

/** 返回独立版本控制 AG-UI 动作地址。 */
function getVersionControlUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
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
    Array.isArray(snapshot.codePaths) &&
    typeof snapshot.headMessage === 'string' &&
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

/** 读取工作区全部 Git 变更（里程碑提醒用，不限定文件范围）。 */
export async function inspectAllVersionControl(
  workspaceRoot: string
): Promise<VersionControlSnapshot> {
  const response = await runVersionControl(
    { action: 'inspect_all', workspaceRoot },
    '读取工作区全部 Git 状态。'
  )
  if (!isVersionControlSnapshot(response.snapshot)) {
    throw new Error('版本控制接口没有返回完整的 Git 状态。')
  }
  return response.snapshot
}

/** 按本次未提交改动生成业务变更说明，用于预填「提交并推送」弹窗的说明框。
 *
 * 生成不出来（无改动、产物缺失、模型失败）时后端返回空串，这里原样返回空串 ——
 * 调用方保持空框让用户自己写，不要把"没生成出来"当成错误弹提示。
 */
export async function summarizeVersionControl(workspaceRoot: string): Promise<string> {
  const response = await runVersionControl(
    { action: 'summarize', workspaceRoot },
    '根据本次改动生成变更说明。'
  )
  return typeof response.changeSummary === 'string' ? response.changeSummary : ''
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

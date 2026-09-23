import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

/**
 * 远端分支动作：新建应用时校验分支名，并检查远端是否已存在同名分支。
 *
 * 分支名同时由后端（services/repository_branch.py）校验，这里的前端校验只是为了
 * 让用户在提交前就看到问题；两边的规则必须保持一致。
 */

export type RemoteBranchCheckResult = {
  action: 'check'
  branchName: string
  exists: boolean
}

/**
 * 模板基线提交后把基线推成远端分支的结果（随 Bootstrap 结果一起返回）。
 *
 * - pushed：分支已建出或已按用户确认覆盖
 * - skipped：未推送。远端分支已存在但用户没确认过覆盖（或应用没配置分支）
 * - failed：推送失败，应用创建与规划不受影响
 */
export type RepositoryBranchOutcome = {
  branchName: string
  status: 'pushed' | 'skipped' | 'failed'
  commitSha: string
  message: string
}

/** 句末标点。后端消息是完整句子（以 。结尾），嵌进前端文案前要去掉。 */
const TRAILING_SENTENCE_PUNCTUATION = /[。．.；;！!？?]+$/

/**
 * 把后端返回的完整句子当成前端文案里的一个**从句**使用。
 *
 * 后端消息本身是完整句子，前端模板会在它后面接着写"应用本身可以正常使用，稍后可重试。"，
 * 直接拼会出现「…TOKEN。。应用本身…」这种两个句号。这里去掉句末标点后再交给调用方拼接。
 *
 * 消息为空时用 `fallback`（fallback 自带完整标点，由调用方决定）。
 */
export function asMessageClause(message: string | undefined, fallback: string): string {
  const text = (message ?? '').trim().replace(TRAILING_SENTENCE_PUNCTUATION, '')
  return text || fallback
}

type RepositoryBranchAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'check' | 'create' | 'delete'
  branchName?: string
  exists?: boolean
  /**
   * create/delete 动作的分支结果状态（created / created_local_only / deleted / …）。
   * 刻意不叫 `status` —— 那是信封的运行态，动作数据里同名字段会把它顶掉。
   */
  branchStatus?: string
  commitSha?: string
  message?: string
  error?: { type?: string; message?: string }
}

/** 返回远端分支 AG-UI 动作地址。 */
function getRepositoryBranchUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/repository-branch/run`
    : '/api/agent/repository-branch/run'
}

/** 校验响应信封并读取远端分支动作结果。 */
function readRepositoryBranchPayload(
  value: unknown
): RepositoryBranchAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<RepositoryBranchAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as RepositoryBranchAgUiPayload
}

/**
 * 校验分支名是否符合 Git 规则；合法时返回 undefined。
 *
 * 规则与后端 validate_branch_name 一致，报错文案直接给用户看。
 */
export function validateBranchName(value: string): string | undefined {
  const branchName = (value ?? '').trim()
  if (!branchName) return '请输入分支名'
  if (branchName.length > 255) return '分支名过长，请控制在 255 个字符以内'
  // Git 明确禁止的字符（见 git-check-ref-format），含空格与控制字符。
  // eslint-disable-next-line no-control-regex
  if (/[\u0000- \u007f~^:?*[\]\\]/.test(branchName)) {
    return '分支名不能包含空格，或 ~ ^ : ? * [ 这类字符'
  }
  if (branchName.includes('..')) return '分支名不能包含连续的两个点'
  if (branchName.includes('@{')) return '分支名不能包含 @{'
  if (branchName.startsWith('-')) return '分支名不能以短横线开头'
  if (branchName.startsWith('/') || branchName.endsWith('/')) {
    return '分支名不能以斜杠开头或结尾'
  }
  if (branchName.includes('//')) return '分支名不能包含连续的两个斜杠'
  if (branchName.endsWith('.') || branchName.endsWith('.lock')) {
    return '分支名不能以点或 .lock 结尾'
  }
  if (branchName === 'HEAD') return '分支名不能叫 HEAD'
  return undefined
}

/** 通过标准 AG-UI 客户端在工作区建出分支并推送到远端。 */
export async function createRepositoryBranch(input: {
  workspaceRoot: string
  branchName: string
  /** 用户是否已确认覆盖远端同名分支。 */
  allowOverwrite?: boolean
  threadId?: string
}): Promise<RepositoryBranchOutcome> {
  const agent = createAgUiHttpAgent({
    url: getRepositoryBranchUrl(),
    threadId: input.threadId || randomUUID()
  })
  agent.addMessage({
    id: randomUUID(),
    role: 'user',
    content: `创建并推送分支 ${input.branchName}。`
  })

  let payload: RepositoryBranchAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'repository-branch') return
      payload = readRepositoryBranchPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload =
        readRepositoryBranchPayload(
          (event.snapshot as { repositoryBranch?: unknown }).repositoryBranch
        ) ?? payload
    }
  }

  const result = await agent.runAgent(
    {
      forwardedProps: {
        repositoryBranch: {
          action: 'create',
          workspaceRoot: input.workspaceRoot,
          branchName: input.branchName,
          allowOverwrite: input.allowOverwrite === true
        }
      }
    },
    subscriber
  )
  payload =
    readRepositoryBranchPayload(
      (result.result as { repositoryBranch?: unknown } | undefined)?.repositoryBranch
    ) ?? payload

  if (!payload) throw new Error('远端版本接口没有返回有效状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '创建版本失败。')
  }
  return {
    branchName: payload.branchName || input.branchName,
    // created=已推到远端；created_local_only=只在本地建出，远端没推上去。
    status: payload.branchStatus === 'created' ? 'pushed' : 'skipped',
    commitSha: payload.commitSha || '',
    message: payload.message || ''
  }
}

/**
 * 重试把工作区当前分支推送到远端（上次因网络等原因失败时用）。
 *
 * 分支名与仓库地址由后端从工作区 `application.json` 读取，所以这里只传工作区。
 */
export async function pushRepositoryBranch(input: {
  workspaceRoot: string
  threadId?: string
}): Promise<RepositoryBranchOutcome> {
  const agent = createAgUiHttpAgent({
    url: getRepositoryBranchUrl(),
    threadId: input.threadId || randomUUID()
  })
  agent.addMessage({
    id: randomUUID(),
    role: 'user',
    content: '重试把当前版本推送到远端仓库。'
  })

  let payload: RepositoryBranchAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'repository-branch') return
      payload = readRepositoryBranchPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload =
        readRepositoryBranchPayload(
          (event.snapshot as { repositoryBranch?: unknown }).repositoryBranch
        ) ?? payload
    }
  }

  const result = await agent.runAgent(
    { forwardedProps: { repositoryBranch: { action: 'push', workspaceRoot: input.workspaceRoot } } },
    subscriber
  )
  payload =
    readRepositoryBranchPayload(
      (result.result as { repositoryBranch?: unknown } | undefined)?.repositoryBranch
    ) ?? payload

  if (!payload) throw new Error('远端版本接口没有返回有效状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '推送失败。')
  }
  const pushed = payload.branchStatus === 'pushed'
  return {
    branchName: payload.branchName || '',
    status: pushed ? 'pushed' : payload.branchStatus === 'skipped' ? 'skipped' : 'failed',
    commitSha: payload.commitSha || '',
    message: payload.message || ''
  }
}

/** 通过标准 AG-UI 客户端检查远端仓库是否已存在该分支。 */
export async function checkRemoteBranch(input: {
  repoUrl: string
  branchName: string
  threadId?: string
}): Promise<RemoteBranchCheckResult> {
  const agent = createAgUiHttpAgent({
    url: getRepositoryBranchUrl(),
    threadId: input.threadId || randomUUID()
  })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: `检查远端是否已存在分支 ${input.branchName}。`
  }
  agent.addMessage(message)

  let payload: RepositoryBranchAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'repository-branch') return
      payload = readRepositoryBranchPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload =
        readRepositoryBranchPayload(
          (event.snapshot as { repositoryBranch?: unknown }).repositoryBranch
        ) ?? payload
    }
  }

  const result = await agent.runAgent(
    {
      forwardedProps: {
        repositoryBranch: {
          action: 'check',
          repoUrl: input.repoUrl,
          branchName: input.branchName
        }
      }
    },
    subscriber
  )
  payload =
    readRepositoryBranchPayload(
      (result.result as { repositoryBranch?: unknown } | undefined)?.repositoryBranch
    ) ?? payload

  if (!payload) throw new Error('远端版本接口没有返回有效状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '远端版本检查失败。')
  }
  if (typeof payload.exists !== 'boolean') {
    throw new Error('远端版本接口没有返回存在性结果。')
  }
  return {
    action: 'check',
    branchName: payload.branchName || input.branchName,
    exists: payload.exists
  }
}

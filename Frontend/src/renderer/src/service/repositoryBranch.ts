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
  branches: string[]
}

/** 远端仓库当前所有分支，供名称避重与推荐共用。 */
export type RemoteBranchListResult = { action: 'list'; branches: string[] }

/**
 * 模板基线提交后把基线推成远端分支的结果（随 Bootstrap 结果一起返回）。
 *
 * - pushed：分支已通过普通推送发布
 * - skipped：应用未配置远端仓库或分支
 * - failed：推送失败，已创建的本地分支保留供重试
 */
export type RepositoryBranchOutcome = {
  branchName: string
  status: 'pushed' | 'skipped' | 'failed'
  commitSha: string
  message: string
}

/** 远端分支显式删除结果；本地分支和提交不由该动作修改。 */
export type RepositoryBranchDeleteResult = {
  branchName: string
  status: 'deleted'
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
  action?: 'list' | 'check' | 'create' | 'push' | 'delete'
  branchName?: string
  exists?: boolean
  branches?: string[]
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
  if (!branchName) return '请输入版本号'
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

/** 允许的仓库地址协议。后端 build_authenticated_remote_url 只认带协议与主机的 URL。 */
const ALLOWED_REPO_PROTOCOLS = new Set(['http:', 'https:', 'ssh:'])

/**
 * 校验码云地址。规则与后端 build_authenticated_remote_url 保持一致：
/**
 * 必须是带协议与主机名的 URL，否则后端拼不出可推送的认证地址。
 *
 * 空值合法（返回 undefined）：码云地址非必填——用户可以只本地开发，先不填远端。
 * 填写了才做格式校验。
 */
export function validateRepoUrl(value: string): string | undefined {
  const repoUrl = (value ?? '').trim()
  // 空 = 不填 = 跳过格式校验。必填约束由表单自身决定，这里不替它判。
  if (!repoUrl) return undefined
  if (/\s/.test(repoUrl)) return '码云地址不能包含空格'
  let parsed: URL
  try {
    parsed = new URL(repoUrl)
  } catch {
    return '码云地址格式无效，请填写完整的 http(s):// 或 ssh:// 地址'
  }
  if (!ALLOWED_REPO_PROTOCOLS.has(parsed.protocol)) {
    return '码云地址只支持 http、https 或 ssh 协议'
  }
  if (!parsed.hostname) return '码云地址缺少主机名'
  return undefined
}

type RepositoryBranchActionInput = {
  action: 'create' | 'push' | 'delete'
  workspaceRoot?: string
  repoUrl?: string
  branchName?: string
  startPoint?: string
  threadId?: string
}

/** 共用分支动作的 AG-UI 生命周期、状态读取与失败信封处理。 */
async function runRepositoryBranchAction(
  input: RepositoryBranchActionInput
): Promise<RepositoryBranchAgUiPayload> {
  const agent = createAgUiHttpAgent({
    url: getRepositoryBranchUrl(),
    threadId: input.threadId || randomUUID()
  })
  const content = input.action === 'create'
    ? `创建并推送分支 ${input.branchName}。`
    : input.action === 'delete'
      ? `删除远端分支 ${input.branchName}。`
      : '重试把当前版本推送到远端仓库。'
  agent.addMessage({ id: randomUUID(), role: 'user', content })

  let payload: RepositoryBranchAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'repository-branch') {
        payload = readRepositoryBranchPayload(event.value) ?? payload
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readRepositoryBranchPayload(
        (event.snapshot as { repositoryBranch?: unknown }).repositoryBranch
      ) ?? payload
    }
  }
  const result = await agent.runAgent({
    forwardedProps: {
      repositoryBranch: {
        action: input.action,
        ...(input.workspaceRoot ? { workspaceRoot: input.workspaceRoot } : {}),
        ...(input.repoUrl ? { repoUrl: input.repoUrl } : {}),
        ...(input.branchName ? { branchName: input.branchName } : {}),
        ...(input.startPoint ? { startPoint: input.startPoint } : {})
      }
    }
  }, subscriber)
  payload = readRepositoryBranchPayload(
    (result.result as { repositoryBranch?: unknown } | undefined)?.repositoryBranch
  ) ?? payload
  if (!payload || payload.action !== input.action) {
    throw new Error('远端版本接口没有返回有效状态。')
  }
  if (payload.status === 'failed') {
    const fallback = input.action === 'create'
      ? '创建版本失败。'
      : input.action === 'delete' ? '删除远端分支失败。' : '推送失败。'
    throw new Error(payload.error?.message || fallback)
  }
  return payload
}

/** 通过标准 AG-UI 客户端在工作区建出分支并推送到远端。 */
export async function createRepositoryBranch(input: {
  workspaceRoot: string
  branchName: string
  /** 历史迭代可选起点；默认从当前 HEAD 建出。 */
  startPoint?: string
  threadId?: string
}): Promise<RepositoryBranchOutcome> {
  const payload = await runRepositoryBranchAction({ ...input, action: 'create' })
  return {
    branchName: payload.branchName || input.branchName,
    // created=已推到远端；created_local_only=只在本地建出，远端没推上去。
    status: payload.branchStatus === 'created' ? 'pushed' : 'skipped',
    commitSha: payload.commitSha || '',
    message: payload.message || ''
  }
}

/** 重试推送当前分支；仓库地址与分支名仍由后端从工作区配置读取。 */
export async function pushRepositoryBranch(input: {
  workspaceRoot: string
  threadId?: string
}): Promise<RepositoryBranchOutcome> {
  const payload = await runRepositoryBranchAction({ ...input, action: 'push' })
  const pushed = payload.branchStatus === 'pushed'
  return {
    branchName: payload.branchName || '',
    status: pushed ? 'pushed' : payload.branchStatus === 'skipped' ? 'skipped' : 'failed',
    commitSha: payload.commitSha || '',
    message: payload.message || ''
  }
}

/** 显式删除远端分支；只有服务端确认 deleted 才视为成功，不修改本地分支或提交。 */
export async function deleteRepositoryBranch(input: {
  repoUrl: string
  branchName: string
  threadId?: string
}): Promise<RepositoryBranchDeleteResult> {
  const payload = await runRepositoryBranchAction({ ...input, action: 'delete' })
  if (payload.branchStatus !== 'deleted') {
    throw new Error(payload.message || '删除远端分支失败，请检查仓库权限后重试。')
  }
  return {
    branchName: payload.branchName || input.branchName,
    status: 'deleted',
    message: payload.message || ''
  }
}

/** 通过标准 AG-UI 客户端读取远端分支，保留完整生命周期与结构化错误。 */
async function readRemoteBranches(input: {
  action: 'list' | 'check'
  repoUrl: string
  branchName?: string
  threadId?: string
}): Promise<RepositoryBranchAgUiPayload> {
  const agent = createAgUiHttpAgent({
    url: getRepositoryBranchUrl(),
    threadId: input.threadId || randomUUID()
  })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: input.action === 'list' ? '读取远端仓库的已有分支。' : `检查远端分支 ${input.branchName}。`
  }
  agent.addMessage(message)
  let payload: RepositoryBranchAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'repository-branch') payload = readRepositoryBranchPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readRepositoryBranchPayload(
        (event.snapshot as { repositoryBranch?: unknown }).repositoryBranch
      ) ?? payload
    }
  }
  const result = await agent.runAgent({
    forwardedProps: {
      repositoryBranch: {
        action: input.action,
        repoUrl: input.repoUrl,
        ...(input.branchName ? { branchName: input.branchName } : {})
      }
    }
  }, subscriber)
  payload = readRepositoryBranchPayload(
    (result.result as { repositoryBranch?: unknown } | undefined)?.repositoryBranch
  ) ?? payload
  if (!payload) throw new Error('远端版本接口没有返回有效状态。')
  if (payload.status === 'failed') throw new Error(payload.error?.message || '远端分支查询失败，请重试。')
  if (payload.action !== input.action || !Array.isArray(payload.branches) ||
    !payload.branches.every((branch) => typeof branch === 'string')) {
    throw new Error('远端版本接口没有返回有效分支列表。')
  }
  return payload
}

/** 读取远端完整分支列表；读取失败不能按空仓库处理。 */
export async function listRemoteBranches(input: {
  repoUrl: string
  threadId?: string
}): Promise<RemoteBranchListResult> {
  const payload = await readRemoteBranches({ ...input, action: 'list' })
  return { action: 'list', branches: payload.branches! }
}

/** 读取完整分支列表并检查目标名称，供提交前复检使用。 */
export async function checkRemoteBranch(input: {
  repoUrl: string
  branchName: string
  threadId?: string
}): Promise<RemoteBranchCheckResult> {
  const payload = await readRemoteBranches({ ...input, action: 'check' })
  if (typeof payload.exists !== 'boolean') throw new Error('远端版本接口没有返回存在性结果。')
  return {
    action: 'check',
    branchName: payload.branchName || input.branchName,
    exists: payload.exists,
    branches: payload.branches!
  }
}

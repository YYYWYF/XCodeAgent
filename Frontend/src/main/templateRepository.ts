export type TemplateCloneAttempt = {
  repositoryUrl: string
  timeoutMs: number
}

const DEFAULT_TEMPLATE_CLONE_TIMEOUT_MS = 120_000
const GITHUB_HTTPS_PROBE_TIMEOUT_MS = 30_000

/** 将支持转换的 GitHub HTTPS 仓库地址转换成等价 SSH 地址。 */
export function githubSshRepositoryUrl(repositoryUrl: string): string | undefined {
  const match = repositoryUrl
    .trim()
    .match(/^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i)
  if (!match) return undefined
  return `git@github.com:${match[1]}/${match[2]}.git`
}

/** 生成最多三次模板拉取方案，GitHub HTTPS 首次不可达时优先尝试等价 SSH。 */
export function templateCloneAttempts(repositoryUrl: string): TemplateCloneAttempt[] {
  const primaryUrl = repositoryUrl.trim()
  const sshUrl = githubSshRepositoryUrl(primaryUrl)
  if (!sshUrl) {
    return Array.from({ length: 3 }, () => ({
      repositoryUrl: primaryUrl,
      timeoutMs: DEFAULT_TEMPLATE_CLONE_TIMEOUT_MS
    }))
  }
  return [
    { repositoryUrl: primaryUrl, timeoutMs: GITHUB_HTTPS_PROBE_TIMEOUT_MS },
    { repositoryUrl: sshUrl, timeoutMs: DEFAULT_TEMPLATE_CLONE_TIMEOUT_MS },
    { repositoryUrl: primaryUrl, timeoutMs: DEFAULT_TEMPLATE_CLONE_TIMEOUT_MS }
  ]
}

/** 规范化 GitHub HTTPS/SSH 仓库身份，供模板来源校验忽略传输协议差异。 */
export function normalizeGitRepositoryIdentity(repositoryUrl: string): string {
  const value = repositoryUrl
    .trim()
    .replace(/\/$/, '')
    .replace(/\.git$/i, '')
  const httpsMatch = value.match(/^https:\/\/github\.com\/(.+)$/i)
  if (httpsMatch) return `github.com/${httpsMatch[1]}`.toLowerCase()
  const sshMatch = value.match(/^git@github\.com:(.+)$/i)
  if (sshMatch) return `github.com/${sshMatch[1]}`.toLowerCase()
  const sshProtocolMatch = value.match(/^ssh:\/\/git@github\.com\/(.+)$/i)
  if (sshProtocolMatch) return `github.com/${sshProtocolMatch[1]}`.toLowerCase()
  return value
}

/** 判断两个 Git 仓库地址是否指向同一仓库。 */
export function gitRepositoriesEquivalent(left: string, right: string): boolean {
  return normalizeGitRepositoryIdentity(left) === normalizeGitRepositoryIdentity(right)
}

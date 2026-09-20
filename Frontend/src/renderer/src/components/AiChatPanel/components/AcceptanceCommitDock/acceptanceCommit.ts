/**
 * 验收阶段"自动保存代码"的判定与文案。
 *
 * 背景：验收是质量门禁通过后的稳定边界，此时把工作区快照成一个独立 commit，
 * 出问题能回滚到"验收通过那一刻"。这与模板 baseline 的自动提交同一性质，
 * 所以也照它的做法把结果**展示**出来 —— 自动提交由平台发起、用户没参与写信息，
 * 不显示就成了黑盒。
 *
 * 与模板 baseline 的区别：那时仓库刚建、工作区确定干净；验收时工作区可能有用户在
 * 外部 IDE 里暂存的内容或敏感文件。所以这里**不是无条件提交**，不满足条件时降级
 * 回手动提醒（用户自己审阅），而不是替用户做决定。
 */

/** 自动提交的消息。带上语义，让历史里能看出这是验收节点。 */
export const ACCEPTANCE_COMMIT_MESSAGE = 'chore: 验收通过，保存当前版本代码'

export type AcceptanceCommitDecision =
  /** 有变更且可安全提交：执行自动提交。 */
  | 'commit'
  /** 工作区干净：没有可保存的内容，静默。 */
  | 'clean'
  /** 不满足自动提交条件：降级为手动提醒，由用户审阅。 */
  | 'fallback'

export function decideAcceptanceCommit(input: {
  /** 已读到 Git 快照。 */
  hasSnapshot: boolean
  /** 读取 Git 状态失败的原因；空串表示没失败。 */
  inspectError: string
  /** 仓库还没有基线提交，不能提交。 */
  unborn: boolean
  /** 已有暂存内容：替用户提交会把他精心挑选的暂存区混进来。 */
  hasStagedChanges: boolean
  /** 变更里含敏感文件（.env/.npmrc/id_rsa 等），后端会拒绝。 */
  hasSensitivePath: boolean
  /** 全部变更文件数。 */
  eligibleCount: number
}): AcceptanceCommitDecision {
  // 读不到状态、或仓库没有基线：交给手动提醒去报错，自动路径不猜。
  if (input.inspectError || !input.hasSnapshot || input.unborn) return 'fallback'
  // 干净工作区：无事可做，也不该提示。
  if (input.eligibleCount === 0) return 'clean'
  // 有暂存内容或有敏感文件：不替用户决定，降级让他自己审阅。
  // （后端对这两种情况都会硬拒绝，所以自动提交即使发起也只会失败。）
  if (input.hasStagedChanges || input.hasSensitivePath) return 'fallback'
  return 'commit'
}

/**
 * 后端会拒绝提交的敏感文件名（与 `workspace.py::SENSITIVE_FILE_NAMES` 一致）。
 *
 * 前端提前判一次是为了走降级路径，而不是等后端报错 —— 后端那道校验仍然是权威的。
 */
const SENSITIVE_FILE_NAMES = new Set([
  '.env',
  '.env.local',
  '.env.development',
  '.env.production',
  '.npmrc',
  '.pypirc',
  '.netrc',
  'id_rsa',
  'id_ed25519'
])

export function hasSensitivePath(paths: readonly string[]): boolean {
  return paths.some((path) => {
    const name = path.replace(/\\/g, '/').split('/').pop() || ''
    return SENSITIVE_FILE_NAMES.has(name)
  })
}

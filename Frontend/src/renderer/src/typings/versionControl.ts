export type VersionControlFile = {
  path: string
  status: string
  indexStatus: string
  worktreeStatus: string
  staged: boolean
  untracked: boolean
}

export type VersionControlSnapshot = {
  workspaceRoot: string
  repositoryRoot: string
  branch: string
  head: string
  fingerprint: string
  dirty: boolean
  hasStagedChanges: boolean
  files: VersionControlFile[]
  requestedPaths: string[]
  eligiblePaths: string[]
  /**
   * `eligiblePaths` 里排除 `.devagentstudio` 平台产物后的业务代码变更。
   *
   * 提交提醒（角标、返回首页确认、各档提醒）按这个口径计数：平台自身的状态流转
   * 与规划产物不该被当成"用户改了代码"。提交弹窗与提交校验仍用 `eligiblePaths`，
   * 所以产物照常可见、可勾选、可提交。
   *
   * 例外：设计阶段的发送前提交门禁要看 `eligiblePaths` ——
   * 那时唯一的变更就是 `.devagentstudio`，按 `codePaths` 算永远是 0，门禁会消失。
   */
  codePaths: string[]
  /**
   * HEAD 的提交信息首行；未建立基线时为空串。
   *
   * 自动提交（模板 baseline、验收）由平台发起、用户没参与写信息，所以界面要把它
   * 显示出来，否则那个 commit 对用户是个黑盒。
   */
  headMessage: string
  unavailablePaths: string[]
}

export type VersionControlCommitResult = {
  action: 'commit'
  workspaceRoot: string
  repositoryRoot: string
  commitSha: string
  message: string
  committedPaths: string[]
  remainingDirty: boolean
  snapshot: VersionControlSnapshot
}

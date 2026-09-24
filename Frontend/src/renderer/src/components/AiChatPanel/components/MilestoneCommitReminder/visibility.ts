/**
 * 提交提醒的显隐判定。
 *
 * 单独成模块而不是内联在组件里：这些条件决定"什么时候该打扰用户"，
 * 是最容易写错、也最值得钉住的部分；组件本身挂了 Git 状态机，不适合直接单测。
 */
export function shouldRenderCommitReminder(input: {
  /** 已提交完成。 */
  hasResult: boolean
  /** 用户已选择"稍后提交"且代码未再变化。 */
  dismissed: boolean
  /** 弱提醒：读不到 Git 状态时静默。 */
  hideWhenUnavailable: boolean
  /** 读取 Git 状态失败的原因；空串表示没失败。 */
  inspectError: string
  inspecting: boolean
  hasSnapshot: boolean
  eligibleCount: number
}): boolean {
  // 已提交或已暂缓：不再打扰。
  if (input.hasResult || input.dismissed) return false
  // 弱提醒在读不到 Git 状态时静默：设计阶段仓库尚未建立（bootstrap 之后才有），
  // 那时"提交"物理上不成立，报错只会制造噪声。弱提醒的定位就是不打断。
  if (input.hideWhenUnavailable && input.inspectError) return false
  // 没有可提交变更：不渲染（首帧尚未读到快照时不要据此判定，否则会闪一下）。
  if (!input.inspecting && input.hasSnapshot && input.eligibleCount === 0) return false
  return true
}

/**
 * 提醒按哪份文件清单计数（决定角标数字与提醒是否出现）。
 *
 * **它不决定弹窗的默认勾选**：弹窗默认全勾选它列出的文件，见 `useMilestoneCommit`
 * 里 `setSelectedPaths` 处的说明。
 *
 * 默认走 `codePaths`（业务代码）：`.devagentstudio` 下的规划产物与状态快照会随每个设计
 * 步骤变化，算进来会让角标在用户一行业务代码都没写时就亮起并持续增长。
 *
 * `includePlatformArtifacts` 只有发送前提交门禁（`useCommitBeforeSend`）会传 true ——
 * 那时唯一的变更就是 `.devagentstudio`，按业务代码算永远是 0，门禁会彻底不出现。
 * 文档 §4.4「推进前门禁」正是把它定位成与"代码提交入口分开"的第二条通道。
 */
export function resolveCommitScope(input: {
  snapshot: { eligiblePaths: string[]; codePaths: string[] } | undefined
  includePlatformArtifacts: boolean
}): string[] {
  const source = input.includePlatformArtifacts
    ? input.snapshot?.eligiblePaths
    : input.snapshot?.codePaths
  return source ?? []
}

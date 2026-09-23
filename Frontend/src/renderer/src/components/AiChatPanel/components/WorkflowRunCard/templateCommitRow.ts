/**
 * 「应用模板已就绪」卡里提交区显示哪一行。
 *
 * 单独成模块而不是内联在组件里：这三分支决定"什么时候该请用户提交、什么时候只该
 * 报告状态"，正是最容易被写错的地方 —— 组件本身挂了 Git 状态机，不适合直接单测。
 *
 * 背景：模板由 bootstrap 在建仓时自动提交（`git_manager.initialize_baseline` 会
 * `git add frontend backend .devagentstudio` 并 commit），所以这张卡出现时通常**已经
 * 没有初始化代码可提交**。早先这里固定显示"建议创建初始化提交 / 当前 0 个文件可提交"
 * 加一个灰按钮，等于建议一个做不到的动作。
 */
export type TemplateCommitRow = 'error' | 'reminder' | 'baseline' | 'none'

export function resolveTemplateCommitRow(input: {
  /** 工作区根目录存在，且用户没有"稍后"、也还没提交过。 */
  showCommitArea: boolean
  /** 读取 Git 状态失败的原因；空串表示没失败。 */
  inspectError: string
  inspecting: boolean
  hasSnapshot: boolean
  /** 当前口径下的可提交文件数（默认只算业务代码）。 */
  eligibleCount: number
}): TemplateCommitRow {
  // 读不到 Git 状态：模板已生成，仓库本该存在，值得报出来并允许重试。
  if (input.showCommitArea && input.inspectError) return 'error'
  // 有业务代码变更才给提交入口 —— 提醒性质，用户可以"稍后"。
  if (input.showCommitArea && input.eligibleCount > 0) return 'reminder'
  // 没有待提交的业务代码：报告基线已就绪。这是事实陈述，**不受"稍后"影响** ——
  // 它不是提醒，所以不看 showCommitArea，否则用户点过"稍后"就再也看不到状态了。
  // 首帧尚未读到快照时不判定，避免闪一下。
  if (!input.inspecting && input.hasSnapshot && input.eligibleCount === 0) return 'baseline'
  return 'none'
}

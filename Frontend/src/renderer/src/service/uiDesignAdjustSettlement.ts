/**
 * 判断一次「调整设计稿」（adjust_pages）是否已经结束，可以收起「生成中」。
 *
 * **为什么不能只看 workflow 状态**：提交那一刻 workflow 还停在上一次的
 * `requires_user_input`（新一轮 run 尚未开始流式），直接按状态判定会把"刚提交"
 * 误判成"已完成"，loading 一闪就没 —— 用户看到的就是"点了没反应"。
 *
 * **为什么不能只靠"先观察到 running"**：流式帧可能被批处理合并，错过 running
 * 就会让进行中态永久卡住 —— 用户看到「一直在生成中」，而提示让他点「刷新」，
 * 那时却连刷新按钮都没有（按钮只在有 queued/generating 页时渲染，adjust 全程
 * 写 confirmed）。
 *
 * 这里用 **runId 换代 + 落回待输入态** 两条一起判：runId 换代证明新一轮 run 真的
 * 开始了（提交时记下的还是上一轮的 id），落回 `requires_user_input` 证明它结束了。
 */
export function shouldSettleAdjust(input: {
  /** 当前是否处于 adjust 进行中（acting 集合里有 'adjust' 哨兵）。 */
  adjusting: boolean
  /** 提交 adjust 时记下的 runId。 */
  submittedRunId: string
  /** 当前 workflow 的 runId。 */
  currentRunId: string
  /** 当前 workflow 的状态。 */
  workflowStatus: string
}): boolean {
  if (!input.adjusting) return false
  // 新一轮 run 尚未开始：runId 还是提交时那个。
  if (input.currentRunId === input.submittedRunId) return false
  // 新一轮还在跑。
  if (input.workflowStatus !== 'requires_user_input') return false
  return true
}

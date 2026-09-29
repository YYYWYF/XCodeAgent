/**
 * 产物（页面 / 接口 / 实体 / UI 设计稿）归属哪一轮迭代的标注口径。
 *
 * 产物的"已开发"只有三档状态，而 completed 事实会跨迭代继承 —— 于是新迭代一进来
 * 满屏都是"已完成"，用户看不出哪些是以前做过的、哪些是本轮要做的。这里把被压掉的
 * 那个维度（**哪一轮完成的**）还原成界面上的标注。
 *
 * 归属用**分支名**（= 用户看到的版本号，如 v1.0），与新建表单里的「版本号
 * （对应码云仓库中的分支名）」是同一个东西。
 *
 * 纯函数集中在这里，便于单测；组件只负责渲染。
 */

export type IterationOrigin = 'current' | 'previous' | 'unknown'

/** 判断某个产物的归属相对当前迭代是什么关系。 */
export function iterationOriginOf(
  artifactBranch: string | undefined | null,
  currentBranch: string | undefined | null
): IterationOrigin {
  const artifact = (artifactBranch ?? '').trim()
  const current = (currentBranch ?? '').trim()
  // 归属缺失时不猜：老工作区、或读不到分支名的情形都落到 unknown，
  // 界面据此不显示标注，而不是误标成"旧迭代"。
  if (!artifact || !current) return 'unknown'
  return artifact === current ? 'current' : 'previous'
}

/**
 * 归属标注文案。
 *
 * - 本轮完成 → 「当前版本」（比「本轮完成」更贴用户心智：用户按版本号理解迭代）
 * - 以前完成 → 「v1.0 已完成」
 * - 未知     → 空串，调用方据此不渲染徽章
 */
export function iterationOriginLabel(
  origin: IterationOrigin,
  branchName: string | undefined | null
): string {
  if (origin === 'current') return '当前版本'
  if (origin === 'previous') {
    const branch = (branchName ?? '').trim()
    return branch ? `${branch} 已完成` : '旧迭代已完成'
  }
  return ''
}

/** 状态已完成、但归属未知时的提示文案（老数据，没有归属标签）。 */
export const UNKNOWN_ORIGIN_LABEL = '已完成'

/**
 * UI 设计稿专用：**历史迭代计划过、但当时没设计**的标注。
 *
 * 用户要能看到"哪个版本该设计却没设计"，所以文案必须带上具体版本名。
 */
export function undesignedOriginLabel(branchName: string | undefined | null): string {
  const branch = (branchName ?? '').trim()
  return branch ? `${branch} 该设计未设计` : '该设计未设计'
}

/** UI 设计稿专用：历史迭代真的产出过设计稿的标注。 */
export function designedOriginLabel(branchName: string | undefined | null): string {
  const branch = (branchName ?? '').trim()
  return branch ? `${branch} 已设计过` : '已设计过'
}

/** UI 设计稿的归属事实（与后端 UiDesignIterationOrigin 对应）。 */
export type UiDesignOrigin = {
  designedIn?: string
  plannedButUndesignedIn?: string
}

/**
 * UI 设计稿归属 → 徽章文案。
 *
 * 优先报"设计过"（它确实做出来过，比"没做"更值得说）；两者都没有则返回空串，
 * 由徽章组件不渲染 —— 那种情况就是"本轮新增"，不需要标注。
 */
export function designOriginLabel(origin?: UiDesignOrigin): string {
  if (!origin) return ''
  if (origin.designedIn) return designedOriginLabel(origin.designedIn)
  if (origin.plannedButUndesignedIn) return undesignedOriginLabel(origin.plannedButUndesignedIn)
  return ''
}

/**
 * UI 设计稿归属 → 徽章的**归属语义**档。
 *
 * 两种设计稿事实都发生在历史迭代里，所以归属都是 previous；未知则返回 unknown，
 * 由徽章组件不渲染。**视觉配色不在这里**，见 designOriginTone。
 */
export function designOriginKind(origin?: UiDesignOrigin): IterationOrigin {
  return origin?.designedIn || origin?.plannedButUndesignedIn ? 'previous' : 'unknown'
}

/**
 * UI 设计稿徽章的**视觉档位**（只决定配色，不改变归属语义）。
 *
 * 刻意与 IterationOrigin 分开：归属仍然是"上一轮做的"，但两种事实的观感必须能一眼分开 ——
 * 「已设计过」是**确实产出过**的好消息，用与"已完成的开发产出"同源的绿色档；
 * 「该设计未设计」是**欠账**，沿用旧迭代的中性档，不额外制造视觉噪音。
 */
export type DesignOriginTone = 'designed' | 'undesigned'

export function designOriginTone(origin?: UiDesignOrigin): DesignOriginTone | undefined {
  if (!origin) return undefined
  if (origin.designedIn) return 'designed'
  if (origin.plannedButUndesignedIn) return 'undesigned'
  return undefined
}

/**
 * 本轮尚无设计稿时，页面行的状态文案。
 *
 * 上一轮设计过的页面本轮会被清空重做，此时 `page.code` 为空。直接显示「未生成」会让用户
 * 以为从来没做过 —— 与旁边的「v1.0 已设计过」徽章自相矛盾。这里把"本轮还没做"和
 * "从来没做过"分开说。
 */
export function uiDesignPendingLabel(origin?: UiDesignOrigin): string {
  return origin?.designedIn ? '本轮待生成' : '未生成'
}

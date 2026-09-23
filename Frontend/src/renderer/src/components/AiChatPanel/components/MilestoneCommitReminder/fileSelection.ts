/**
 * 提交弹窗里"选择文件"的批量勾选。
 *
 * 单独成模块而不是内联在组件里：全选/清空的边界（空列表、首帧尚未读到快照）最容易写错，
 * 而组件本身挂了提交流程，不适合直接单测。
 */

/** 是否已全选。空列表返回 false —— 没有文件时"全选"没有意义。 */
export function areAllSelected(input: {
  allPaths: readonly string[]
  selectedPaths: readonly string[]
}): boolean {
  if (input.allPaths.length === 0) return false
  return input.allPaths.every((path) => input.selectedPaths.includes(path))
}

/**
 * 「全选」按钮的下一态：已全选则清空，否则全选。
 *
 * 做成切换而不是单纯的"全选"：全选后再点一次毫无反应，会让人以为按钮坏了。
 * 清空由同一个按钮承担，所以不再单独提供"反选" —— 单个勾选已经覆盖挑几个的场景。
 */
export function toggleSelectAll(input: {
  allPaths: readonly string[]
  selectedPaths: readonly string[]
}): string[] {
  return areAllSelected(input) ? [] : [...input.allPaths]
}

/**
 * 提交弹窗里可勾选的文件是否都在"可提交范围"内。
 *
 * 这是提交能成功的前提：后端校验 `selected ⊆ requested`，一旦弹窗列出的文件超出
 * requested，用户勾上就会被拒"所选文件已不属于当前可提交变更"。
 *
 * 曾经的 bug：`requestedPaths` 被直接别名成提醒口径（默认只含业务代码），而弹窗列出
 * 的是全部变更 —— 于是点"全选"必然失败，只有不勾 .devagentstudio 才能提交。
 * 这个断言把"提醒口径 ≠ 可提交范围"钉住。
 */
export function isSelectionScopeValid(input: {
  /** 弹窗列出的全部文件。 */
  allPaths: readonly string[]
  /** 后端允许提交的范围。 */
  requestedPaths: readonly string[]
}): boolean {
  return input.allPaths.every((path) => input.requestedPaths.includes(path))
}

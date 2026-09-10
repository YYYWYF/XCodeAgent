/** 判断设计生成过程中是否需要向用户提供手动刷新入口。 */
export function shouldShowUiDesignRefresh(
  actingPageIds: string[],
  generatingPageIds: string[]
): boolean {
  return actingPageIds.length > 0 || generatingPageIds.length > 0
}

type UiDesignProgressPage = { pageId?: string; status?: string }

/** 等待 UI 设计恢复请求结束，并在成功或失败时统一收敛本地刷新态。 */
export async function settleUiDesignRefresh(
  submit: () => void | Promise<void>,
  onSettled: () => void
): Promise<void> {
  try {
    await submit()
  } finally {
    onSettled()
  }
}

/** 手动刷新后以最新清单终态清理已完成的本地动作标记。 */
export function reconcileActingPageIdsAfterRefresh(
  actingPageIds: string[],
  pages: UiDesignProgressPage[]
): string[] {
  const generatingIds = new Set(
    pages
      .filter((page) => page.status === 'queued' || page.status === 'generating')
      .map((page) => page.pageId || '')
      .filter(Boolean)
  )
  return actingPageIds.filter((pageId) =>
    pageId === 'adjust' ? generatingIds.size > 0 : generatingIds.has(pageId)
  )
}

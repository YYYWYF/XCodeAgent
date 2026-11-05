/** 本地历史分页只影响展示；运行身份、后台状态和确认门保持独立。 */
export type SessionHistoryPage = {
  before?: number
  /** 未遍历全文时未知，不为了精确计数阻塞工作台。 */
  total: number | null
  contextMessageIds: number[]
  loadedMessageIds: number[]
  messagePositions: Record<number, number>
}

export const SESSION_HISTORY_PAGE_SIZE = 10

import type { WorkflowEvent } from '../typings'

/** 一次设计稿生成进度：最新文案 + 当前正在生成的那一页。 */
export type UiDesignGenerationProgress = {
  /** 后端写好的进度文案，如「正在调整设计稿（第 1/2 页）：概览页」。 */
  message: string
  /** 当前正在生成的那一页；该页已收尾、或后端未报出具体页时为 undefined。 */
  pageId?: string
}

/** UI 确认节点名：只有它的进度与本卡片相关。 */
const UI_CONFIRMATION_NODE = 'ui_confirmation'

/**
 * 从 workflow 事件流里读出 UI 设计稿的生成进度。
 *
 * **为什么需要它**：用户在调整输入框里没 `@` 指定页面时，前端在提交那一刻根本不知道
 * 后端会改哪几页（由后端按指令自行判断），于是卡片上哪一行都不亮「生成中」—— 用户看到
 * 的是"预览变了、状态没动"。后端其实**逐页**推了进度，只是事件类型对不上：
 *
 * - 节点用 `get_stream_writer()` 发的是 `ui_confirmation.progress`；
 * - runtime 把它转成 `workflow.node.progress` 才进 workflow.events。
 *
 * 早先这里找的是前者，所以永远读不到（旧注释据此断言"该字段恒为 null"）。现在按后者读，
 * 并额外要求 `detail.pageId` —— runtime 会把节点写的 `adjust_current` 一并映射成 pageId。
 *
 * 判定"当前页"要区分"开始"与"收尾"两类进度：两者都不带 pageId 之外的区分标记，
 * 但收尾那条的 `ready` 会比开始那条大 1（开始是第 N 页，收尾是第 N 页已完成）。
 * 所以取最后一个带 pageId 的事件，若之后有更大的 ready，说明它已经做完了。
 */
export function uiDesignGenerationProgress(
  events: WorkflowEvent[] | undefined
): UiDesignGenerationProgress | undefined {
  let message = ''
  /** 最后一个"某页开始生成"的进度。 */
  let started: { pageId: string; ready: number } | undefined
  /** 已见到的最大 ready，用来判断 started 是否已经收尾。 */
  let maxReady = -1

  for (const event of events || []) {
    if (event.type !== 'workflow.node.progress') continue
    if (event.nodeName !== UI_CONFIRMATION_NODE) continue
    const detail = (event.data?.detail ?? {}) as Record<string, unknown>
    const ready = typeof detail.ready === 'number' ? detail.ready : -1
    if (ready > maxReady) maxReady = ready
    // 只认字符串：pageId 一定来自后端的页面清单（字符串）。别的类型宁可忽略，
    // 也不要拼出一个匹配不上任何行的假 id 去点亮某一页。
    const pageId = typeof detail.pageId === 'string' ? detail.pageId.trim() : ''
    if (pageId) started = { pageId, ready }
    if (event.message) message = event.message
  }

  if (!message && !started) return undefined
  // 该页已经收尾（后续进度把 ready 推得更大）→ 不再算作生成中，避免收尾那一瞬还挂着。
  const pageId = started && maxReady <= started.ready ? started.pageId : undefined
  return { message, pageId }
}

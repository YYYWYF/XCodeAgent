/** 提及解析需要的页面最小信息（结构兼容页面卡片的 PageDesign）。 */
export type MentionPage = {
  pageId?: string
  name?: string
}

/** 解析结果：命中的 pageId 列表，以及剥掉提及后的调整指令。 */
export type DesignMentions = {
  pageIds: string[]
  instruction: string
}

/**
 * 从「调整设计稿」输入框的文本里解析 `@页面` 提及，映射回 pageId。
 *
 * **为什么不能按空白切分**：页面名可以带空格（产品计划里的 `Hello World`，
 * 而 pageId 是 `hello_world`）。早先的实现用 `/@([^\s@]+)/` 抓提及，遇到
 * `@Hello World 帮我把文案改成粉色` 只会抓出 `Hello`，匹配不到任何页面，
 * pageIds 于是为空。后果不是"没调整"，而是**卡片上该页不显示「生成中」**：
 * 无 pageIds 时后端按 instruction 自行判断调整哪些页，设计稿照常更新，
 * 只有前端这一侧的按页加载态丢了 —— 用户看到"预览变了，但状态没动"。
 *
 * 所以改成**按已知页面名/ID 做最长匹配**：页面清单是有限集合，逐个试比按分隔符
 * 切更准确，也顺带支持手打 `@` 而不只是浮层插入。最长优先是为了让
 * `Hello World` 赢过同为前缀的 `Hello`。
 */
export function parseDesignMentions(input: { text: string; pages: MentionPage[] }): DesignMentions {
  const { text, pages } = input
  // 候选提及词：页面名与 pageId 都认（用户可能手打 id）。去重后按长度倒序，
  // 保证同一位置上更长的名字先被匹配到。
  const candidates = Array.from(
    new Set(
      pages
        .flatMap((page) => [page.name, page.pageId])
        .map((value) => String(value || '').trim())
        .filter(Boolean)
    )
  ).sort((a, b) => b.length - a.length)

  const pageIds: string[] = []
  // 命中的提及在原文里的区间，最后统一从指令里剥掉。
  const spans: Array<{ start: number; end: number }> = []

  let cursor = text.indexOf('@')
  while (cursor >= 0) {
    const afterAt = cursor + 1
    // 用 slice 比较而不是 startsWith：大小写不敏感时也能保证命中的原文片段
    // 恰好是 candidate.length 个字符，后面按这个长度切片才不会错位。
    const matched = candidates.find(
      (candidate) =>
        text.slice(afterAt, afterAt + candidate.length).toLowerCase() === candidate.toLowerCase()
    )
    if (!matched) {
      cursor = text.indexOf('@', afterAt)
      continue
    }
    const page = pages.find((item) => item.name === matched || item.pageId === matched)
    // 名字大小写与清单不一致时上面的 find 会落空，退一步按不区分大小写再找一次。
    const resolved =
      page ??
      pages.find(
        (item) =>
          String(item.name || '').toLowerCase() === matched.toLowerCase() ||
          String(item.pageId || '').toLowerCase() === matched.toLowerCase()
      )
    const pageId = String(resolved?.pageId || '')
    if (pageId && !pageIds.includes(pageId)) pageIds.push(pageId)
    let end = afterAt + matched.length
    // 顺带吃掉紧跟其后的一个空格：浮层插入的就是 `@名字 `，留着会与前面的空格
    // 拼成双空格；提及紧跟在标点后时（`布局，@名字 再放大`）也能少一个残留空格。
    if (text[end] === ' ') end += 1
    spans.push({ start: cursor, end })
    cursor = text.indexOf('@', end)
  }

  // 从后往前删，避免前面的区间下标被前一次删除带偏。
  let instruction = text
  for (const span of [...spans].reverse()) {
    instruction = instruction.slice(0, span.start) + instruction.slice(span.end)
  }
  return { pageIds, instruction: instruction.replace(/\s+/g, ' ').trim() }
}

import type { AgentChatMessage } from '../types'
import type { SessionHistoryPage } from '../../../../../shared/sessionHistory'

type CacheEntry = {
  generation: object
  messages: AgentChatMessage[]
  revision: number
  savedRevision: number
  pending: number
  bytes: number
  used: number
  history?: SessionHistoryPage
}
export type SessionSaveToken = {
  key: string
  entry?: CacheEntry
  revision: number
  loadedMessageIds?: number[]
  savedMessageIds: number[]
}
const EMPTY_MESSAGES: AgentChatMessage[] = []
const BYTE_BUDGET = 32 * 1024 * 1024

/** 浅比较不可变消息快照，兼容保存调用方新建数组/消息外壳而共享内部快照的情况。 */
function sameMessages(left: AgentChatMessage[], right: AgentChatMessage[]): boolean {
  return (
    left === right ||
    (left.length === right.length &&
      left.every((message, index) => {
        const other = right[index]
        const keys = Object.keys(message) as Array<keyof AgentChatMessage>
        const otherKeys = Object.keys(other) as Array<keyof AgentChatMessage>
        return [...new Set([...keys, ...otherKeys])].every((key) => message[key] === other[key])
      }))
  )
}

/** 只在载入/保存边界估算正文体积；去重共享对象并在超预算后停止，不在 token 更新时序列化。 */
function estimateBytes(value: unknown, seen = new WeakSet<object>()): number {
  if (typeof value === 'string') return value.length * 2
  if (!value || typeof value !== 'object') return 8
  if (seen.has(value)) return 0
  seen.add(value)
  let total = 32
  for (const [key, child] of Object.entries(value)) {
    total += key.length * 2 + estimateBytes(child, seen)
    if (total > BYTE_BUDGET) break
  }
  return total
}

/** 待确认、暂停和未知工作流先留在内存，避免缓存回收改变恢复/交接语义。 */
function hasUnsettledWorkflow(messages: AgentChatMessage[]): boolean {
  if (messages.some((message) => message.planningLoading)) return true
  const workflow = [...messages].reverse().find((message) => message.workflow)?.workflow
  return Boolean(
    workflow &&
      !['completed', 'failed', 'cancelled', 'ended'].includes(workflow.summary.status || '')
  )
}

/** 保存版本与引用保留独立于后台执行；只回收可从原会话文件重新读取的正文。 */
export class SessionMessageCache {
  private entries = new Map<string, CacheEntry>()
  private pins = new Map<string, number>()
  private clock = 0

  /** 限制非当前、无占用的已保存会话数量与估算体积；保护项不受预算强制驱逐。 */
  constructor(
    private readonly maxIdle = 3,
    private readonly maxBytes = BYTE_BUDGET
  ) {}

  /** 查询正文是否真实驻留；空正文与缓存缺失必须区分。 */
  has(key: string): boolean {
    return this.entries.has(key)
  }

  /** 读取时更新最近使用顺序，不修改 React 状态。 */
  read(key: string): AgentChatMessage[] {
    const entry = this.entries.get(key)
    if (!entry) return EMPTY_MESSAGES
    entry.used = ++this.clock
    return entry.messages
  }

  /** 从磁盘注册干净正文；迟到的读取不能覆盖已有内存内容。 */
  restore(key: string, messages: AgentChatMessage[], history?: SessionHistoryPage): void {
    if (this.entries.has(key)) return
    this.entries.set(key, {
      generation: {},
      messages,
      history,
      revision: 0,
      savedRevision: 0,
      pending: 0,
      bytes: estimateBytes(messages),
      used: ++this.clock
    })
  }

  /** 获取分页游标；完整正文与仅加载尾页的会话不能混用覆盖保存。 */
  history(key: string): SessionHistoryPage | undefined {
    return this.entries.get(key)?.history
  }

  /** 合并较早历史，不覆盖流式更新，也不把读盘操作标为用户修改。 */
  prepend(key: string, messages: AgentChatMessage[], page: SessionHistoryPage): void {
    const entry = this.entries.get(key)
    if (!entry?.history) return
    const known = new Set(entry.history.loadedMessageIds)
    const positions = { ...entry.history.messagePositions, ...page.messagePositions }
    // 上翻页天然早于当前窗口；保存会改变字节偏移，不用两次文件版本的偏移混合排序。
    const older = messages.filter((message) => !entry.messages.some((item) => item.id === message.id) && !known.has(message.id))
    entry.messages = [...older, ...entry.messages]
    const visible = new Set(messages.map((message) => message.id))
    entry.history = {
      ...page,
      messagePositions: positions,
      contextMessageIds: entry.history.contextMessageIds.filter((id) => !visible.has(id)),
      loadedMessageIds: [...new Set([...entry.history.loadedMessageIds, ...page.loadedMessageIds])]
    }
    entry.bytes = estimateBytes(entry.messages)
  }

  /** 消息变化立即标脏，直至对应版本得到成功落盘回执。 */
  update(key: string, messages: AgentChatMessage[]): void {
    const entry = this.entries.get(key)
    if (entry?.messages === messages) return
    let history = entry?.history
    if (
      history?.before !== undefined &&
      !messages.some((message) => message.id === history!.before)
    ) {
      const hidden = new Set(history.contextMessageIds)
      const next = messages.find(
        (message) => !hidden.has(message.id) && history!.messagePositions[message.id] !== undefined
      )
      if (next) history = { ...history, before: next.id }
    }
    this.entries.set(
      key,
      entry
        ? { ...entry, messages, history, revision: entry.revision + 1, used: ++this.clock }
        : {
            generation: {},
            messages,
            revision: 1,
            savedRevision: -1,
            pending: 0,
            bytes: 0,
            used: ++this.clock
          }
    )
  }

  /** 当前视图、加载或交接临时保留正文；释放只使其重新具备候选资格。 */
  retain(key: string): () => void {
    this.pins.set(key, (this.pins.get(key) || 0) + 1)
    let released = false
    return () => {
      if (released) return
      released = true
      const count = (this.pins.get(key) || 1) - 1
      if (count) this.pins.set(key, count)
      else this.pins.delete(key)
    }
  }

  /** 保存前记录精确消息版本；保存期间继续流入的新消息不能被旧回执标为干净。 */
  beginSave(key: string, messages: AgentChatMessage[]): SessionSaveToken {
    const entry = this.entries.get(key)
    if (entry) entry.pending++
    return {
      key,
      entry,
      loadedMessageIds: entry?.history ? [...entry.history.loadedMessageIds] : undefined,
      savedMessageIds: messages.map((message) => message.id),
      revision: entry && sameMessages(entry.messages, messages) ? entry.revision : -1
    }
  }

  /** 保存失败维持脏状态；已回收并重载的同名条目不接受旧实例的回执。 */
  finishSave(token: SessionSaveToken, success: boolean): void {
    const current = this.entries.get(token.key)
    // update 使用新外壳但保留 pending，因此用版本与当前条目共同判断，删除重建则单独隔离。
    if (!current || current.generation !== token.entry?.generation || current.pending === 0) return
    current.pending--
    if (success && current.history) {
      current.history = {
        ...current.history,
        loadedMessageIds: [
          ...new Set([...current.history.loadedMessageIds, ...token.savedMessageIds])
        ]
      }
    }
    if (success && token.revision >= 0 && current.revision === token.revision) {
      current.savedRevision = token.revision
      current.bytes = estimateBytes(current.messages)
    } else if (!success || token.revision < current.savedRevision) {
      current.savedRevision = -1
    }
  }

  /** 仅淘汰无保护、已保存、已收口的旧正文，调用方同时释放闲置客户端。 */
  evict(protectedByExecution: (key: string) => boolean): string[] {
    const candidates = [...this.entries]
      .filter(
        ([key, entry]) =>
          !this.pins.has(key) &&
          !entry.pending &&
          entry.revision === entry.savedRevision &&
          !protectedByExecution(key) &&
          !hasUnsettledWorkflow(entry.messages)
      )
      .sort(([, left], [, right]) => right.used - left.used)
    let bytes = 0
    let kept = 0
    const evicted: string[] = []
    candidates.forEach(([key, entry]) => {
      if (kept >= this.maxIdle || bytes + entry.bytes > this.maxBytes) {
        this.entries.delete(key)
        evicted.push(key)
      } else {
        bytes += entry.bytes
        kept++
      }
    })
    return evicted
  }

  /** 显式删除与项目清理沿原语义移除正文，不触碰磁盘文件。 */
  remove(key: string): void {
    this.entries.delete(key)
  }

  /** 提供已驻留身份供工作区清理，不暴露或复制大段正文。 */
  keys(): string[] {
    return [...this.entries.keys()]
  }
}

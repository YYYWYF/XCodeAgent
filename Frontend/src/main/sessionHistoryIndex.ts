import fs from 'node:fs/promises'
import { writeSessionAtomically } from './sessionPersistence'
import { normalizePersistentSessionMessage } from './sessionMessageNormalization'
import { SESSION_HISTORY_PAGE_SIZE, type SessionHistoryPage } from '../shared/sessionHistory'

type RecordValue = Record<string, unknown>
/** 只读取对象形式的可选控制字段，拒绝数组和非对象输入。 */
function asRecord(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordValue) : {}
}

type MessageRange = { start: number; end: number; id: number }
type SessionIndex = {
  size: number
  mtime: number
  signature: string
  metadata: RecordValue
  ranges: MessageRange[]
  context: number[]
}

/** 扫过一个 JSON 值，记录原文件的 UTF-8 字节边界，不依赖消息文本内容。 */
function valueEnd(bytes: Buffer, start: number): number {
  let depth = 0,
    quoted = false,
    escaped = false
  for (let index = start; index < bytes.length; index++) {
    const char = bytes[index]
    if (quoted) {
      if (escaped) escaped = false
      else if (char === 92) escaped = true
      else if (char === 34) {
        quoted = false
        if (!depth) return index + 1
      }
      continue
    }
    if (char === 34) quoted = true
    else if (char === 123 || char === 91) depth++
    else if (char === 125 || char === 93) {
      if (!depth) return index
      if (--depth === 0) return index + 1
    } else if (!depth && (char === 44 || char === 10 || char === 13 || char === 32)) return index
  }
  return bytes.length
}

/** 跳过当前 JSON 格式允许的空白及字段分隔符。 */
function skip(bytes: Buffer, start: number): number {
  while ([9, 10, 13, 32, 44, 58].includes(bytes[start])) start++
  return start
}

/** 为原有当前格式 JSON 建立可丢弃索引；不会迁移、覆盖或另存聊天正文。 */
function buildIndex(bytes: Buffer, size: number, mtime: number, signature: string): SessionIndex {
  const record = JSON.parse(bytes.toString('utf8')) as RecordValue
  const { messages: rawMessages, ...metadata } = record
  if (!Array.isArray(rawMessages)) throw new Error('session messages must be an array')
  const messages = rawMessages as RecordValue[]
  let position = skip(bytes, 1),
    arrayStart = -1
  while (position < bytes.length && bytes[position] !== 125) {
    const keyEnd = valueEnd(bytes, position)
    const key = JSON.parse(bytes.subarray(position, keyEnd).toString())
    const start = skip(bytes, keyEnd)
    if (key === 'messages') {
      arrayStart = start
      break
    }
    position = skip(bytes, valueEnd(bytes, start))
  }
  if (arrayStart < 0 || bytes[arrayStart] !== 91) throw new Error('session messages missing')
  const ranges: MessageRange[] = []
  position = skip(bytes, arrayStart + 1)
  while (bytes[position] !== 93 && position < bytes.length) {
    const end = valueEnd(bytes, position)
    const message = messages[ranges.length]
    if (!message || !Number.isFinite(Number(message.id)))
      throw new Error('invalid message identity')
    ranges.push({ start: position, end, id: Number(message.id) })
    position = skip(bytes, end)
  }
  // 额外保留当前控制上下文，避免较早的确认/开发目标因展示分页而丢失。
  const latest = new Map<string, number>()
  const byRun = new Map<string, number>()
  messages.forEach((message: RecordValue, index: number) => {
    for (const key of [
      'workflow',
      'revisionHandoff',
      'developmentContinuation',
      'approval',
      'error'
    ]) {
      if (message[key]) latest.set(key, index)
    }
    if (message.workflowCardResult === 'confirmed') latest.set('confirmed', index)
    if (!message.workflow) return
    const workflow = asRecord(message.workflow)
    if (typeof workflow.runId === 'string') byRun.set(workflow.runId, index)
    const summary = asRecord(workflow.summary)
    const state = asRecord(workflow.state),
      result = asRecord(workflow.result)
    if (!['casual_chat', 'workspace_question'].includes(String(summary.intent)))
      latest.set('business', index)
    if (
      summary.buildExecutionScope ||
      summary.testTarget ||
      state.selectedPageId ||
      state.selectedEndpointId ||
      result.selectedPageId ||
      result.selectedEndpointId
    ) {
      latest.set('target', index)
    }
    if (summary.templatePreparation || state.templatePreparation) latest.set('template', index)
  })
  const context = new Set(latest.values())
  for (const index of [...context]) {
    const workflow = asRecord(messages[index].workflow)
    const control = asRecord(
      asRecord(workflow.summary).planControl ||
        asRecord(workflow.state).planControl ||
        asRecord(workflow.result).planControl
    )
    const source =
      typeof control.targetRunId === 'string' ? byRun.get(control.targetRunId) : undefined
    if (source !== undefined) context.add(source)
    for (let preceding = index - 1; preceding >= 0; preceding--) {
      if (messages[preceding].role === 'user') {
        context.add(preceding)
        break
      }
    }
  }
  return { size, mtime, signature, metadata, ranges, context: [...context].sort((a, b) => a - b) }
}

/** 索引按原文件大小与修改时间校验；失效时仅在存储 Worker 内重建。 */
export async function readSessionIndex(file: string): Promise<SessionIndex> {
  const stat = await fs.stat(file)
  const signature = `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`
  try {
    const cached = JSON.parse(await fs.readFile(`${file}.index`, 'utf8')) as SessionIndex
    if (
      cached.size === stat.size &&
      cached.mtime === stat.mtimeMs &&
      cached.signature === signature &&
      Array.isArray(cached.ranges) &&
      Array.isArray(cached.context) &&
      cached.metadata &&
      cached.ranges.every(
        (range) =>
          Number.isSafeInteger(range.id) &&
          Number.isSafeInteger(range.start) &&
          Number.isSafeInteger(range.end) &&
          range.start >= 0 &&
          range.end > range.start &&
          range.end <= stat.size
      ) &&
      cached.context.every(
        (position) => Number.isInteger(position) && position >= 0 && position < cached.ranges.length
      )
    )
      return cached
  } catch {
    /* 索引是派生缓存，缺失或损坏不改变原会话。 */
  }
  const index = buildIndex(await fs.readFile(file), stat.size, stat.mtimeMs, signature)
  await writeSessionAtomically(`${file}.index`, index)
  return index
}

/** 会话目录只返回小摘要，不把消息正文或工作流跨进程复制给列表。 */
export async function readIndexedSummary(file: string): Promise<RecordValue> {
  const index = await readSessionIndex(file)
  return { ...index.metadata, messageCount: index.ranges.length }
}

/** 按稳定消息 ID 向前读取，已存在索引时只读取本页和必要的控制上下文。 */
export async function readIndexedPage(
  file: string,
  before?: number,
  limit = SESSION_HISTORY_PAGE_SIZE
): Promise<{ session: RecordValue; history: SessionHistoryPage }> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('invalid history limit')
  const index = await readSessionIndex(file)
  const end =
    before === undefined
      ? index.ranges.length
      : index.ranges.findIndex((item) => item.id === before)
  if (end < 0) throw new Error('历史位置已变化，请重新打开会话。')
  const start = Math.max(0, end - limit)
  const positions = new Set(Array.from({ length: end - start }, (_, offset) => start + offset))
  const context = before === undefined ? index.context.filter((position) => position < start) : []
  context.forEach((position) => positions.add(position))
  const messages: RecordValue[] = []
  const handle = await fs.open(file, 'r')
  try {
    for (const position of [...positions].sort((a, b) => a - b)) {
      const range = index.ranges[position]
      const bytes = Buffer.alloc(range.end - range.start)
      let offset = 0
      while (offset < bytes.length) {
        const { bytesRead } = await handle.read(
          bytes,
          offset,
          bytes.length - offset,
          range.start + offset
        )
        if (!bytesRead) throw new Error('会话读取不完整，请重试。')
        offset += bytesRead
      }
      messages.push(normalizePersistentSessionMessage(JSON.parse(bytes.toString('utf8'))))
    }
  } finally {
    await handle.close()
  }
  return {
    session: { ...index.metadata, messages },
    history: {
      before: start > 0 ? index.ranges[start].id : undefined,
      total: index.ranges.length,
      contextMessageIds: context.map((position) => index.ranges[position].id),
      loadedMessageIds: messages.map((message) => Number(message.id)),
      messagePositions: Object.fromEntries(
        [...positions].map((position) => [index.ranges[position].id, position])
      )
    }
  }
}

/** 合并已加载范围的修改，未加载正文保持原样；空数组也不能误删未读取的历史。 */
export async function saveIndexedSession(
  file: string,
  session: RecordValue,
  loadedMessageIds?: number[]
): Promise<RecordValue> {
  const incomingMessages = session.messages as RecordValue[]
  session = { ...session, messages: incomingMessages.map(normalizePersistentSessionMessage) }
  let next = session
  if (loadedMessageIds) {
    const existing = JSON.parse(await fs.readFile(file, 'utf8')) as RecordValue
    const loaded = new Set(loadedMessageIds)
    const incoming = new Map<number, RecordValue>(
      (session.messages as RecordValue[]).map((message) => [Number(message.id), message])
    )
    const messages: RecordValue[] = []
    for (const message of existing.messages as RecordValue[]) {
      if (incoming.has(Number(message.id))) {
        messages.push(incoming.get(Number(message.id))!)
        incoming.delete(Number(message.id))
      } else if (!loaded.has(Number(message.id))) messages.push(message)
    }
    messages.push(...incoming.values())
    next = { ...session, messages }
  }
  await writeSessionAtomically(file, next)
  return readIndexedSummary(file)
}

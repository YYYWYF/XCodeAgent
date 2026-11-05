import fs from 'node:fs/promises'
import { normalizePersistentSessionMessage } from './sessionMessageNormalization'
import { SESSION_HISTORY_PAGE_SIZE, type SessionHistoryPage } from '../shared/sessionHistory'
import { SessionFileReader, SESSION_PAGE_BYTES, type JsonRecord } from './sessionFileReader'
import { fileSignature, writeSessionFile } from './sessionFileWriter'

type TailPosition = { start: number; ordinal: number }
const tailPositions = new Map<string, { signature: string; positions: Map<number, TailPosition> }>()

/** 只缓存最近访问位置，不创建全文索引；文件替换后自动失效，条目数始终有界。 */
function positionsFor(file: string, signature: string): Map<number, TailPosition> {
  let cached = tailPositions.get(file)
  if (cached?.signature !== signature) cached = { signature, positions: new Map() }
  tailPositions.delete(file)
  tailPositions.set(file, cached)
  if (tailPositions.size > 8) tailPositions.delete(tailPositions.keys().next().value!)
  return cached.positions
}

/** 为一次读取绑定文件快照；返回前检测磁盘替换，不混用两个版本的消息。 */
async function withSessionFile<T>(
  file: string,
  read: (reader: SessionFileReader, signature: string) => Promise<T>
): Promise<T> {
  const handle = await fs.open(file, 'r')
  try {
    const stat = await handle.stat()
    const result = await read(new SessionFileReader(handle, stat.size), fileSignature(stat))
    if (fileSignature(await fs.stat(file)) !== fileSignature(stat)) {
      throw new Error('会话在读取期间已变化，请重试。')
    }
    return result
  } finally {
    await handle.close()
  }
}

/** 列表只读文件头；非空消息总数未知，不为计数扫描正文或建立索引。 */
export async function readSessionSummary(file: string): Promise<JsonRecord> {
  return withSessionFile(file, async (reader) => {
    const { metadata, arrayStart } = await reader.header()
    const first = await reader.skip(arrayStart + 1, 1)
    return { ...metadata, messageCount: (await reader.byte(first)) === 93 ? 0 : null }
  })
}

/** 从尾部读取最近十条；单页预算只减少条数，选中的单条消息始终完整解码。 */
export async function readSessionPage(
  file: string,
  before?: number,
  limit = SESSION_HISTORY_PAGE_SIZE
): Promise<{ session: JsonRecord; history: SessionHistoryPage }> {
  if (!Number.isInteger(limit) || limit < 1 || limit > SESSION_HISTORY_PAGE_SIZE) {
    throw new Error('invalid history limit')
  }
  return withSessionFile(file, async (reader, signature) => {
    const { metadata, arrayStart } = await reader.header()
    const messages: JsonRecord[] = []
    const positions: Record<number, number> = {}
    const loadedMessageIds: number[] = []
    const offsets = positionsFor(file, signature)
    const anchor = before === undefined ? undefined : offsets.get(before)
    let found = before === undefined || Boolean(anchor),
      count = anchor?.ordinal || 0,
      bytes = 0,
      more = false
    for await (const range of reader.reverse(
      arrayStart,
      anchor?.start ?? (await reader.arrayEnd())
    )) {
      const identity = await reader.identity(range)
      count++
      offsets.set(identity.id, { start: range.start, ordinal: count })
      if (offsets.size > 256) offsets.delete(offsets.keys().next().value!)
      if (!found) {
        if (identity.id === before) found = true
        continue
      }
      const length = range.end - range.start
      if (
        messages.length >= limit ||
        (messages.length > 0 && bytes + length > SESSION_PAGE_BYTES)
      ) {
        more = true
        break
      }
      const rawMessage = JSON.parse((await reader.range(range.start, range.end)).toString('utf8'))
      const message = normalizePersistentSessionMessage(rawMessage)
      loadedMessageIds.push(identity.id)
      bytes += length
      messages.unshift(message)
      positions[identity.id] = range.start
      // 达到条数后仅查看左侧分隔符，不为了判断 hasMore 再扫描第十一条巨型消息。
      if (messages.length >= limit) {
        more = (await reader.skip(range.start - 1, -1)) > arrayStart
        break
      }
    }
    if (!found) throw new Error('历史位置已变化，请重新打开会话。')
    return {
      session: { ...metadata, messages },
      history: {
        before: more ? Number(messages[0].id) : undefined,
        total: more ? null : count,
        contextMessageIds: [],
        loadedMessageIds,
        messagePositions: positions
      }
    }
  })
}

/** 保存时按块复制未加载正文，不全量解析原 JSON，也不重建完整派生索引。 */
export async function saveSession(
  file: string,
  session: JsonRecord,
  loadedMessageIds?: number[]
): Promise<JsonRecord> {
  const count = await writeSessionFile(file, session, loadedMessageIds)
  const { messages: _messages, history: _history, ...metadata } = session
  return { ...metadata, messageCount: count }
}

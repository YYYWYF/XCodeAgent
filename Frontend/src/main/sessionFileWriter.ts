import { storeWorkflowArtifacts } from './sessionWorkflowArtifacts'
import crypto from 'node:crypto'
import fs, { type FileHandle } from 'node:fs/promises'
import { normalizePersistentSessionMessage } from './sessionMessageNormalization'
import { publishSessionFile } from './sessionPersistence'
import {
  SessionFileReader,
  SESSION_FILE_BLOCK_BYTES,
  type JsonRecord,
  type MessageRange
} from './sessionFileReader'

/** 比较文件身份，拒绝覆盖保存开始后被其它调用者替换或修改的正文。 */
export function fileSignature(stat: Awaited<ReturnType<typeof fs.stat>>): string {
  return `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`
}

/** 按固定块复制未读取历史，包含超大消息时也不构造其字符串或对象。 */
async function copyRange(
  reader: SessionFileReader,
  output: FileHandle,
  range: MessageRange
): Promise<void> {
  for (let position = range.start; position < range.end; position += SESSION_FILE_BLOCK_BYTES) {
    await writeAll(
      output,
      await reader.range(position, Math.min(range.end, position + SESSION_FILE_BLOCK_BYTES))
    )
  }
}

/** 文件写入允许短写，必须等待全部字节写完才推进消息分隔符或原子发布。 */
async function writeAll(output: FileHandle, value: string | Buffer): Promise<void> {
  const bytes = typeof value === 'string' ? Buffer.from(value, 'utf8') : value
  for (let offset = 0; offset < bytes.length; ) {
    const { bytesWritten } = await output.write(bytes, offset, bytes.length - offset)
    if (!bytesWritten) throw new Error('会话文件写入不完整。')
    offset += bytesWritten
  }
}

/** 流式合并已加载范围并原子发布，未加载消息保持原始字节。 */
export async function writeSessionFile(
  file: string,
  session: JsonRecord,
  loadedMessageIds?: number[]
): Promise<number> {
  const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`
  const { messages: rawMessages, history: _history, ...metadata } = session
  const messages = rawMessages as JsonRecord[]
  const incoming = new Map(messages.map((message) => [Number(message.id), message]))
  const loaded = new Set(loadedMessageIds)
  let source: FileHandle | undefined
  let signature: string | undefined
  let count = 0
  const output = await fs.open(temporary, 'wx')
  try {
    await writeAll(
      output,
      `${JSON.stringify(metadata).slice(0, -1)}${Object.keys(metadata).length ? ',' : ''}"messages":[`
    )
    /** 每条消息单独序列化，分隔符不依赖已加载历史数量。 */
    const append = async (message: JsonRecord): Promise<void> => {
      if (count++) await writeAll(output, ',')
      await writeAll(output, JSON.stringify(await storeWorkflowArtifacts(session.workspaceRoot, normalizePersistentSessionMessage(message))))
    }
    if (loadedMessageIds !== undefined) {
      source = await fs.open(file, 'r')
      const stat = await source.stat()
      signature = fileSignature(stat)
      const reader = new SessionFileReader(source, stat.size)
      const { arrayStart } = await reader.header()
      for await (const range of reader.forward(arrayStart, await reader.arrayEnd())) {
        const { id } = await reader.identity(range)
        const replacement = incoming.get(id)
        if (replacement) {
          await append(replacement)
          incoming.delete(id)
        } else if (!loaded.has(id)) {
          if (count++) await writeAll(output, ',')
          await copyRange(reader, output, range)
        }
      }
    }
    for (const message of incoming.values()) await append(message)
    await writeAll(output, ']}\n')
    await output.close()
    // 复制完即释放原文件句柄，避免 Windows 原子替换时仍持有自己的读取句柄。
    await source?.close()
    source = undefined
    await publishSessionFile(temporary, file, async () => {
      // 每次重试都核对同一个源快照，等待占用释放期间的外部修改也不能被覆盖。
      if (signature && fileSignature(await fs.stat(file)) !== signature) {
        throw new Error('会话在保存期间已变化，请重新打开后重试。')
      }
    })
    return count
  } finally {
    await source?.close()
    await output.close().catch(() => undefined)
    await fs.unlink(temporary).catch(() => undefined)
  }
}

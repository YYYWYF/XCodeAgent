import type { FileHandle } from 'node:fs/promises'

export type JsonRecord = Record<string, unknown>
export type MessageRange = { start: number; end: number }
export const SESSION_FILE_BLOCK_BYTES = 64 * 1024
export const SESSION_PAGE_BYTES = 4 * 1024 * 1024
const HEADER_BYTES = 256 * 1024

/** 在有限缓冲区中寻找完整 JSON 值；跨块未结束时返回空值。 */
function valueEnd(bytes: Buffer, start: number): number | undefined {
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
    } else if (char === 34) quoted = true
    else if (char === 123 || char === 91) depth++
    else if (char === 125 || char === 93) {
      if (!depth) return index
      if (--depth === 0) return index + 1
    } else if (!depth && [9, 10, 13, 32, 44].includes(char)) return index
  }
  return undefined
}

/** 跳过 JSON 空白，字段分隔符由调用方显式校验。 */
function skip(bytes: Buffer, position: number): number {
  while ([9, 10, 13, 32].includes(bytes[position])) position++
  return position
}

/** 解析消息数组之前的完整顶层字段，不在字符串内容中搜索 messages。 */
function parseHeader(bytes: Buffer): { metadata: JsonRecord; arrayStart: number } | undefined {
  let position = skip(bytes, 0)
  if (bytes[position++] !== 123) throw new Error('会话文件不是 JSON 对象。')
  const metadata: JsonRecord = {}
  while (position < bytes.length) {
    position = skip(bytes, position)
    if (bytes[position] !== 34) return undefined
    const keyEnd = valueEnd(bytes, position)
    if (keyEnd === undefined) return undefined
    const key = JSON.parse(bytes.subarray(position, keyEnd).toString('utf8')) as string
    position = skip(bytes, keyEnd)
    if (bytes[position++] !== 58) return undefined
    position = skip(bytes, position)
    if (key === 'messages') {
      if (position >= bytes.length) return undefined
      if (bytes[position] !== 91) throw new Error('会话缺少消息数组。')
      return { metadata, arrayStart: position }
    }
    const end = valueEnd(bytes, position)
    if (end === undefined) return undefined
    Object.defineProperty(metadata, key, {
      value: JSON.parse(bytes.subarray(position, end).toString('utf8')),
      enumerable: true,
      configurable: true,
      writable: true
    })
    position = skip(bytes, end)
    if (position >= bytes.length) return undefined
    if (bytes[position++] !== 44) throw new Error('会话消息数组必须位于顶层字段之后。')
  }
  return undefined
}

/** 只保留一个固定大小磁盘块；正向保存与反向分页共用结构扫描器。 */
export class SessionFileReader {
  private block: Buffer = Buffer.alloc(0)
  private blockStart = -1

  /** 绑定单次读取的文件句柄和大小，原子替换不会混入另一份正文。 */
  constructor(
    readonly handle: FileHandle,
    readonly size: number
  ) {}

  /** 命中块时同步取值，避免大消息扫描为每个字节创建 Promise。 */
  private cached(position: number): number | undefined {
    return position >= this.blockStart && position < this.blockStart + this.block.length
      ? this.block[position - this.blockStart]
      : undefined
  }

  /** 按位置读取单字节，跨块时复用固定预算缓冲区。 */
  async byte(position: number): Promise<number> {
    if (position < 0 || position >= this.size) return -1
    if (position < this.blockStart || position >= this.blockStart + this.block.length) {
      this.blockStart = Math.floor(position / SESSION_FILE_BLOCK_BYTES) * SESSION_FILE_BLOCK_BYTES
      this.block = await this.range(
        this.blockStart,
        Math.min(this.size, this.blockStart + SESSION_FILE_BLOCK_BYTES)
      )
    }
    return this.block[position - this.blockStart]
  }

  /** 精确读取选定消息或扫描块的字节范围，不调用全文件 readFile。 */
  async range(start: number, end: number): Promise<Buffer> {
    const bytes = Buffer.alloc(end - start)
    let offset = 0
    while (offset < bytes.length) {
      const { bytesRead } = await this.handle.read(
        bytes,
        offset,
        bytes.length - offset,
        start + offset
      )
      if (!bytesRead) throw new Error('会话读取不完整，请重新打开。')
      offset += bytesRead
    }
    return bytes
  }

  /** 仅解析受限文件头；元数据过大显式报错，不退回全量解析。 */
  async header(): Promise<{ metadata: JsonRecord; arrayStart: number }> {
    for (
      let length = Math.min(this.size, 4096);
      ;
      length = Math.min(this.size, HEADER_BYTES, length * 2)
    ) {
      const parsed = parseHeader(await this.range(0, length))
      if (parsed) return parsed
      if (length >= Math.min(this.size, HEADER_BYTES)) throw new Error('会话文件头过大或不完整。')
    }
  }

  /** 跳过指定方向的空白和数组元素分隔符。 */
  async skip(position: number, direction: 1 | -1): Promise<number> {
    while ([9, 10, 13, 32, 44].includes(await this.byte(position))) position += direction
    return position
  }

  /** 校验当前格式的文件尾，仅允许 messages 数组后结束顶层对象。 */
  async arrayEnd(): Promise<number> {
    let position = await this.skip(this.size - 1, -1)
    if ((await this.byte(position--)) !== 125) throw new Error('会话文件尚未完整写入。')
    position = await this.skip(position, -1)
    if ((await this.byte(position)) !== 93) throw new Error('会话文件尾缺少消息数组。')
    return position
  }

  /** 从数组尾部逐条寻找对象边界；反向引号通过左侧反斜杠奇偶判断转义。 */
  async *reverse(arrayStart: number, arrayEnd: number): AsyncGenerator<MessageRange> {
    let position = await this.skip(arrayEnd - 1, -1)
    while (position > arrayStart) {
      if ((await this.byte(position)) !== 125) throw new Error('会话消息必须为 JSON 对象。')
      const end = position + 1
      let depth = 0,
        quoted = false
      for (; position > arrayStart; position--) {
        const char = this.cached(position) ?? (await this.byte(position))
        if (char === 34) {
          let preceding = position - 1
          while ((this.cached(preceding) ?? (await this.byte(preceding))) === 92) preceding--
          if ((position - preceding - 1) % 2 === 0) quoted = !quoted
        } else if (!quoted) {
          if (char === 125 || char === 93) depth++
          else if ((char === 123 || char === 91) && --depth === 0) break
        }
      }
      if (position <= arrayStart || quoted) throw new Error('会话消息边界不完整。')
      yield { start: position, end }
      position = await this.skip(position - 1, -1)
    }
  }

  /** 正向逐条定位保存范围，扫描时不构造消息正文对象或全文件索引。 */
  async *forward(arrayStart: number, arrayEnd: number): AsyncGenerator<MessageRange> {
    let position = await this.skip(arrayStart + 1, 1)
    while (position < arrayEnd) {
      if ((await this.byte(position)) !== 123) throw new Error('会话消息必须为 JSON 对象。')
      const start = position
      let depth = 0,
        quoted = false,
        escaped = false
      for (; position < arrayEnd; position++) {
        const char = this.cached(position) ?? (await this.byte(position))
        if (quoted) {
          if (escaped) escaped = false
          else if (char === 92) escaped = true
          else if (char === 34) quoted = false
        } else if (char === 34) quoted = true
        else if (char === 123 || char === 91) depth++
        else if ((char === 125 || char === 93) && --depth === 0) break
      }
      if (position >= arrayEnd || quoted) throw new Error('会话消息边界不完整。')
      yield { start, end: position + 1 }
      position = await this.skip(position + 1, 1)
    }
  }

  /** 从消息开头提取身份，巨型 content 或快照不会被解码；当前写入将 id/role 放在正文前。 */
  async identity(range: MessageRange): Promise<{ id: number; role: string }> {
    const bytes = await this.range(range.start, Math.min(range.end, range.start + 4096))
    let position = skip(bytes, 1)
    const fields: JsonRecord = {}
    while (position < bytes.length && (fields.id === undefined || fields.role === undefined)) {
      const keyEnd = valueEnd(bytes, position)
      if (keyEnd === undefined) break
      const key = JSON.parse(bytes.subarray(position, keyEnd).toString('utf8')) as string
      position = skip(bytes, keyEnd)
      if (bytes[position++] !== 58) break
      position = skip(bytes, position)
      const end = valueEnd(bytes, position)
      if (end === undefined) break
      fields[key] = JSON.parse(bytes.subarray(position, end).toString('utf8'))
      position = skip(bytes, end)
      if (bytes[position] === 44) position = skip(bytes, position + 1)
    }
    if (!Number.isSafeInteger(fields.id) || !['user', 'assistant'].includes(String(fields.role))) {
      throw new Error('会话消息开头缺少有效身份。')
    }
    return { id: Number(fields.id), role: String(fields.role) }
  }
}

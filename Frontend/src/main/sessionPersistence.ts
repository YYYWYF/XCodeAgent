import crypto from 'node:crypto'
import fs from 'node:fs/promises'

/** 原子发布完整会话，阶段交接读取列表时只能看到保存前或保存后的完整记录。 */
export async function writeSessionAtomically(sessionFile: string, session: unknown): Promise<void> {
  // 临时文件不以 .json 结尾，列表扫描不会把尚未完成的内容当作会话。
  const temporaryFile = `${sessionFile}.${process.pid}.${crypto.randomUUID()}.tmp`
  try {
    const output = await fs.open(temporaryFile, 'w')
    try {
      if (session && typeof session === 'object' && !Array.isArray(session)
        && Array.isArray((session as Record<string, unknown>).messages)) {
        const { messages, ...metadata } = session as Record<string, unknown> & { messages: unknown[] }
        // 完整会话仍是一份原子 JSON；逐条消息编码，避免构造数百 MB 的整份 JSON 字符串。
        const header = JSON.stringify(metadata)
        await output.writeFile(`${header.slice(0, -1)}${header.length > 2 ? ',' : ''}"messages":[`)
        for (let index = 0; index < messages.length; index += 1) {
          await output.writeFile(`${index ? ',' : ''}${JSON.stringify(messages[index]) ?? 'null'}`)
        }
        await output.writeFile(']}\n')
      } else {
        await output.writeFile(`${JSON.stringify(session)}\n`)
      }
    } finally {
      await output.close()
    }
    await fs.rename(temporaryFile, sessionFile)
  } finally {
    await fs.rm(temporaryFile, { force: true }).catch(() => undefined)
  }
}

import crypto from 'node:crypto'
import fs from 'node:fs/promises'

/** 原子发布完整会话，阶段交接读取列表时只能看到保存前或保存后的完整记录。 */
export async function writeSessionAtomically(sessionFile: string, session: unknown): Promise<void> {
  // 临时文件不以 .json 结尾，列表扫描不会把尚未完成的内容当作会话。
  const temporaryFile = `${sessionFile}.${process.pid}.${crypto.randomUUID()}.tmp`
  try {
    await fs.writeFile(temporaryFile, `${JSON.stringify(session, null, 2)}\n`, 'utf8')
    await fs.rename(temporaryFile, sessionFile)
  } finally {
    await fs.rm(temporaryFile, { force: true }).catch(() => undefined)
  }
}

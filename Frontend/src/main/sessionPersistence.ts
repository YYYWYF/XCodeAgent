import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import { setTimeout as delay } from 'node:timers/promises'

/** 原子替换会话文件；Windows 短暂占用时有限重试，发布前始终重新校验源文件。 */
export async function publishSessionFile(
  temporaryFile: string,
  sessionFile: string,
  validateSource?: () => Promise<void>
): Promise<void> {
  const retryDelays = [50, 100, 200, 400, 800]
  for (let attempt = 0; ; attempt++) {
    await validateSource?.()
    try {
      await fs.rename(temporaryFile, sessionFile)
      return
    } catch (error) {
      // 只重试 Windows 的文件占用类错误；保留原文件，不删除目标或退化为覆盖写。
      const code = (error as NodeJS.ErrnoException).code || ''
      if (
        process.platform !== 'win32' ||
        !['EPERM', 'EACCES', 'EBUSY'].includes(code) ||
        attempt >= retryDelays.length
      ) {
        throw error
      }
      await delay(retryDelays[attempt])
    }
  }
}

/** 原子发布完整会话，阶段交接读取列表时只能看到保存前或保存后的完整记录。 */
export async function writeSessionAtomically(sessionFile: string, session: unknown): Promise<void> {
  // 临时文件不以 .json 结尾，列表扫描不会把尚未完成的内容当作会话。
  const temporaryFile = `${sessionFile}.${process.pid}.${crypto.randomUUID()}.tmp`
  try {
    await fs.writeFile(temporaryFile, `${JSON.stringify(session, null, 2)}\n`, 'utf8')
    await publishSessionFile(temporaryFile, sessionFile)
  } finally {
    await fs.rm(temporaryFile, { force: true }).catch(() => undefined)
  }
}

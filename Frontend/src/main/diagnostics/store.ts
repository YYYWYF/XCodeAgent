import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

export type DiagnosticStatus =
  | 'running'
  | 'normal_exit'
  | 'second_instance'
  | 'update_exit'
  | 'startup_failed'
  | 'abnormal_exit'
  | 'interrupted'

export type DiagnosticField = string | number | boolean | null

type RunSummary = {
  id: string
  startedAt: string
  endedAt?: string
  pid: number
  version: string
  platform: string
  arch: string
  mode: 'packaged' | 'source-dev'
  stage: string
  status: DiagnosticStatus
  exitCode?: number
}

const eventLimit = 2 * 1024 * 1024
const historyLimit = 200 * 1024 * 1024
const historyAge = 14 * 24 * 60 * 60 * 1000
const runName = /^run-[0-9a-f-]{36}$/

/** 同步保存关键记录，确保强制退出后仍可读取已写入内容。 */
function writeDurable(file: string, data: string, flags: 'a' | 'w'): void {
  const fd = fs.openSync(file, flags, 0o600)
  try {
    fs.writeFileSync(fd, data)
    fs.fsyncSync(fd)
  } finally {
    fs.closeSync(fd)
  }
}

/** 原子替换摘要，避免进程中途终止时破坏上一份记录。 */
function writeSummary(directory: string, summary: RunSummary): void {
  const temporary = path.join(directory, `summary-${process.pid}.tmp`)
  writeDurable(temporary, JSON.stringify(summary, null, 2) + '\n', 'w')
  fs.renameSync(temporary, path.join(directory, 'summary.json'))
}

/** 只把明确不存在的进程视为已退出，权限错误和 PID 复用都保留记录。 */
function processIsGone(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return false
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'ESRCH'
  }
}

/** 计算单次运行的诊断占用，跳过文件系统链接。 */
function directoryBytes(directory: string): number {
  return fs.readdirSync(directory, { withFileTypes: true }).reduce((bytes, entry) => {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) return bytes + directoryBytes(file)
    return entry.isFile() ? bytes + fs.lstatSync(file).size : bytes
  }, 0)
}

/** 读取当前格式的摘要，拒绝无法确认归属的记录。 */
function readSummary(directory: string, name: string): RunSummary | undefined {
  const file = path.join(directory, 'summary.json')
  const stat = fs.lstatSync(file)
  if (!stat.isFile() || stat.size > 8192) return undefined
  const summary = JSON.parse(fs.readFileSync(file, 'utf8')) as RunSummary | null
  if (
    summary?.id === name &&
    Number.isInteger(summary.pid) &&
    summary.pid > 0 &&
    typeof summary.startedAt === 'string' &&
    Number.isFinite(Date.parse(summary.startedAt))
  )
    return summary
  return undefined
}

/** 诊断保留上限覆盖安装包和源码开发的运行目录。 */
function listRunDirectories(root: string): string[] {
  const roots = [root]
  const development = path.join(root, 'source-dev')
  if (fs.lstatSync(development, { throwIfNoEntry: false })?.isDirectory()) roots.push(development)
  return roots.flatMap((directory) =>
    fs
      .readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && runName.test(entry.name))
      .map((entry) => path.join(directory, entry.name))
  )
}

/** 每次运行独立写入事件、摘要和原生诊断目录。 */
export class DiagnosticStore {
  readonly directory: string
  private readonly summary: RunSummary
  private eventBytes = 0

  /** 建立当前运行记录，确保其他实例不能覆盖本次日志。 */
  constructor(
    private readonly root: string,
    version: string,
    packaged: boolean
  ) {
    const id = 'run-' + randomUUID()
    this.directory = packaged ? path.join(root, id) : path.join(root, 'source-dev', id)
    this.summary = {
      id,
      startedAt: new Date().toISOString(),
      pid: process.pid,
      version,
      platform: process.platform,
      arch: process.arch,
      mode: packaged ? 'packaged' : 'source-dev',
      stage: 'bootstrap',
      status: 'running'
    }
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 })
    writeSummary(this.directory, this.summary)
    this.event('startup')
  }

  /** 追加事件，达到单文件上限时保留完整的最近事件。 */
  event(event: string, fields: Record<string, DiagnosticField> = {}, critical = true): void {
    const file = path.join(this.directory, 'events.jsonl')
    const line = JSON.stringify({ time: new Date().toISOString(), event, ...fields }) + '\n'
    const bytes = Buffer.byteLength(line)
    if (this.eventBytes + bytes > eventLimit) {
      const previous = fs.readFileSync(file)
      const boundary = previous.indexOf(10, Math.floor(eventLimit / 2))
      const tail = boundary < 0 ? Buffer.alloc(0) : previous.subarray(boundary + 1)
      const temporary = file + '.tmp'
      writeDurable(temporary, tail.toString('utf8') + line, 'w')
      fs.renameSync(temporary, file)
      this.eventBytes = tail.length + bytes
      return
    }
    if (critical) writeDurable(file, line, 'a')
    else fs.appendFileSync(file, line, { mode: 0o600 })
    this.eventBytes += bytes
  }

  /** 保存最新阶段，供突然退出后的摘要读取。 */
  stage(stage: string): void {
    this.summary.stage = stage
    writeSummary(this.directory, this.summary)
    this.event('stage', { stage })
  }

  /** 记录 Electron 明确报告的结束原因和退出码。 */
  finish(status: DiagnosticStatus, exitCode: number): void {
    this.summary.status = status
    this.summary.endedAt = new Date().toISOString()
    this.summary.exitCode = exitCode
    this.event('exit', { status, exitCode })
    writeSummary(this.directory, this.summary)
  }

  /** 恢复已退出的旧会话，优先清理过期和最旧的已结束记录。 */
  maintain(now = Date.now()): void {
    const completed: { directory: string; time: number; bytes: number }[] = []
    let totalBytes = 0
    for (const directory of listRunDirectories(this.root)) {
      try {
        const summary = readSummary(directory, path.basename(directory))
        const isGone = summary && processIsGone(summary.pid)
        if (summary?.status === 'running' && isGone) {
          summary.status = 'interrupted'
          summary.endedAt = new Date(now).toISOString()
          writeSummary(directory, summary)
        }
        const bytes = directoryBytes(directory)
        totalBytes += bytes
        if (
          directory !== this.directory &&
          summary?.endedAt &&
          summary.status !== 'running' &&
          isGone &&
          // 为刚退出的 Crashpad 留出写转储的时间。
          now - Date.parse(summary.endedAt) >= 60000
        ) {
          completed.push({
            directory,
            bytes,
            time: Date.parse(summary.startedAt)
          })
        }
      } catch {
        // 单条记录可能仍在写入或已被移除，继续维护其余历史。
      }
    }
    completed.sort((first, second) => first.time - second.time)
    for (const run of completed) {
      if (now - run.time <= historyAge && totalBytes <= historyLimit) break
      try {
        fs.rmSync(run.directory, { recursive: true, force: true })
        totalBytes -= run.bytes
      } catch {
        // 文件不可清理时继续处理其他记录。
      }
    }
  }
}

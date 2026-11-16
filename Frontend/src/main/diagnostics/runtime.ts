import { app, crashReporter, type BrowserWindow } from 'electron'
import fs from 'node:fs'
import path from 'node:path'
import { DEVAGENTSTUDIO_ENV } from '../env'
import { describeDiagnosticError } from './errors'
import { DiagnosticStore, type DiagnosticField, type DiagnosticStatus } from './store'

let store: DiagnosticStore | undefined
let exitStatus: DiagnosticStatus = 'normal_exit'

/** 日志失败时保持应用正常启动。 */
function safely(action: (diagnostics: DiagnosticStore) => void): void {
  if (!store) return
  try {
    action(store)
  } catch {
    // 诊断目录不可写或磁盘已满时不能影响应用。
  }
}

/** 写入已筛选的诊断字段。 */
export function recordDiagnostic(event: string, fields?: Record<string, DiagnosticField>): void {
  safely((diagnostics) => diagnostics.event(event, fields))
}

/** 保存不包含原始消息和输入内容的异常类型、错误码和调用栈。 */
export function recordDiagnosticError(event: string, error: unknown): void {
  recordDiagnostic(event, describeDiagnosticError(error))
}

/** 保存最新的启动或运行阶段。 */
export function setDiagnosticStage(stage: string): void {
  safely((diagnostics) => diagnostics.stage(stage))
}

/** 设置计划退出原因，实际退出时才提交摘要。 */
export function setDiagnosticExitStatus(status: DiagnosticStatus): void {
  exitStatus = status
}

/** 提供本次会话目录，供同一客户端启动的冻结后端保存致命堆栈。 */
export function getDiagnosticRunDirectory(): string | undefined {
  return store?.directory
}

/** 主实例检查上一次中断，并定期按保留期限整理不同运行的记录。 */
export function maintainDiagnostics(): void {
  const maintain = (): void => safely((diagnostics) => diagnostics.maintain())
  setImmediate(maintain).unref()
  setInterval(maintain, 5 * 60 * 1000).unref()
}

/** 记录窗口加载及卡死信号，不读取 URL、页面数据或窗口标题。 */
function wireWindow(window: BrowserWindow): void {
  const contents = window.webContents
  const webContentsId = contents.id
  window.on('unresponsive', () => recordDiagnostic('window_unresponsive', { webContentsId }))
  window.on('responsive', () => recordDiagnostic('window_responsive', { webContentsId }))
  contents.on('did-finish-load', () => recordDiagnostic('window_loaded', { webContentsId }))
  contents.on('did-fail-load', (_event, errorCode, _description, _url, isMainFrame) => {
    if (isMainFrame && errorCode !== -3)
      recordDiagnostic('window_load_failed', { webContentsId, errorCode })
  })
}

/** 汇总 Chrome 各进程的内存数据，单位为 KiB。 */
function sampleMemory(): void {
  safely((diagnostics) => {
    const metrics = app.getAppMetrics()
    const byType: Record<string, number> = {}
    for (const { type, memory } of metrics) {
      byType[type] = (byType[type] ?? 0) + memory.workingSetSize
    }
    diagnostics.event(
      'memory_sample',
      { processCount: metrics.length, workingSetKiB: JSON.stringify(byType) },
      false
    )
  })
}

/** 在业务入口前启动 Windows/macOS 本地诊断和 Crashpad。 */
export function initializeDiagnostics(): void {
  if (process.platform !== 'win32' && process.platform !== 'darwin') return
  const root = path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR, 'logs', 'diagnostics')
  try {
    store = new DiagnosticStore(root, app.getVersion(), app.isPackaged)
  } catch {
    // 无法创建诊断目录时继续启动，并避免将转储写入未约定的位置。
    return
  }

  safely((diagnostics) => {
    try {
      const dumps = path.join(diagnostics.directory, 'crashpad')
      fs.mkdirSync(dumps, { mode: 0o700 })
      app.setPath('crashDumps', dumps)
      crashReporter.start({ uploadToServer: false })
      diagnostics.event('crashpad_started')
    } catch (error) {
      diagnostics.event('crashpad_start_failed', describeDiagnosticError(error))
    }
  })

  process.on('uncaughtExceptionMonitor', (error, origin) => {
    recordDiagnostic('main_uncaught_exception', { ...describeDiagnosticError(error), origin })
  })
  app.on('render-process-gone', (_event, contents, details) => {
    recordDiagnostic('render_process_gone', {
      webContentsId: contents.id,
      reason: details.reason,
      exitCode: details.exitCode
    })
  })
  app.on('child-process-gone', (_event, details) => {
    recordDiagnostic('child_process_gone', {
      type: details.type,
      reason: details.reason,
      exitCode: details.exitCode
    })
  })
  app.on('browser-window-created', (_event, window) => wireWindow(window))
  app.once('ready', () => {
    setDiagnosticStage('electron_ready')
    sampleMemory()
    setInterval(sampleMemory, 15000).unref()
  })
  app.on('quit', (_event, exitCode) => {
    const status = exitCode !== 0 && exitStatus === 'normal_exit' ? 'abnormal_exit' : exitStatus
    safely((diagnostics) => diagnostics.finish(status, exitCode))
  })
}

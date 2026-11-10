import { spawn, type ChildProcess } from 'node:child_process'

/** 停止当前客户端创建的内置后台及其进程树，正常退出和启动失败共用同一回收入口。 */
export async function stopBackendProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  if (process.platform === 'win32') {
    const killed = await new Promise<boolean>((resolve) => {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true
      })
      killer.once('exit', (code) => resolve(code === 0))
      killer.once('error', () => resolve(false))
    })
    if (killed && (await waitForProcessExit(child, 5000))) return
  }
  signalBackendProcess(child, 'SIGTERM')
  if (!(await waitForProcessExit(child, 5000)) && child.exitCode === null) {
    signalBackendProcess(child, 'SIGKILL')
    if (!(await waitForProcessExit(child, 5000))) throw new Error('内置后台未能退出，请重试。')
  }
}

/** POSIX 清理当前客户端的独立进程组，Windows 回退到直接句柄。 */
function signalBackendProcess(child: ChildProcess, signal: NodeJS.Signals): void {
  if (!child.pid) return
  if (process.platform === 'win32') {
    child.kill(signal)
    return
  }
  try {
    process.kill(-child.pid, signal)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') child.kill(signal)
  }
}

/** 等待内置后台实际退出，监听器和超时统一收口。 */
function waitForProcessExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      child.off('exit', onExit)
      resolve(false)
    }, timeoutMs)
    /** 进程退出后清理超时并报告完成。 */
    const onExit = (): void => {
      clearTimeout(timeout)
      resolve(true)
    }
    child.once('exit', onExit)
  })
}

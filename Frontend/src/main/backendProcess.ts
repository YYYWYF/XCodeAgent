import { spawn, type ChildProcess } from 'node:child_process'

/** 只用于尚未向页面发布就绪的本次启动子进程，不能用来终止已接管或正在执行业务的后台。 */
export async function stopStartingBackend(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.killed) return
  if (process.platform === 'win32') {
    const killed = await new Promise<boolean>((resolve) => {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
      killer.once('exit', (code) => resolve(code === 0))
      killer.once('error', () => resolve(false))
    })
    if (killed || child.exitCode !== null) return
  }
  signalStartingBackend(child, 'SIGTERM')
  if (!(await waitForProcessExit(child, 5000)) && child.exitCode === null) {
    signalStartingBackend(child, 'SIGKILL')
    await waitForProcessExit(child, 5000)
  }
}

/** POSIX 清理本次失败启动的独立进程组，Windows 回退到直接句柄。 */
function signalStartingBackend(child: ChildProcess, signal: NodeJS.Signals): void {
  if (!child.pid) return
  if (process.platform === 'win32') { child.kill(signal); return }
  try { process.kill(-child.pid, signal) }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ESRCH') child.kill(signal) }
}

/** 等待失败启动进程实际退出，监听器和超时统一收口。 */
function waitForProcessExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timeout = setTimeout(() => { child.off('exit', onExit); resolve(false) }, timeoutMs)
    /** 进程退出后清理超时并报告完成。 */
    const onExit = (): void => { clearTimeout(timeout); resolve(true) }
    child.once('exit', onExit)
  })
}

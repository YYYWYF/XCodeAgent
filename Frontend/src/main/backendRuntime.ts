import fs from 'node:fs/promises'
import path from 'node:path'
import { readBackendHealth } from './backendEnvironment'
import type { AppEnv } from './env'
import { controlBackendRuntime, retireBackendRuntime, waitBackendExit } from './backendRuntimeClient'

export type BackendRuntime = { pid: number; baseUrl: string; backendInstanceId: string;
  appEnv: AppEnv; executablePath: string; executableMtimeMs: number; controlToken: string }

/** 只接受当前环境、可核对进程和实例身份的内置后端；未知活进程不能被重启覆盖。 */
export async function readReusableBackend(file: string, appEnv: AppEnv, executablePath: string, clientId: string): Promise<BackendRuntime | undefined> {
  let raw: unknown
  try { raw = JSON.parse(await fs.readFile(file, 'utf8')) }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined; throw error }
  const current = raw as BackendRuntime
  if (!current || !Number.isInteger(current.pid) || current.pid <= 0 ||
    !/^http:\/\/127\.0\.0\.1:\d+$/.test(current.baseUrl) || !current.backendInstanceId || current.appEnv !== appEnv ||
    typeof current.controlToken !== 'string' || current.controlToken.length < 32) {
    throw new Error('后台服务登记无效，无法安全接管，请检查本地后端运行状态。')
  }
  try { process.kill(current.pid, 0) }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ESRCH') return undefined; throw error }
  const health = await readBackendHealth(current.baseUrl, appEnv)
  if (!health || health.backendInstanceId !== current.backendInstanceId || health.backendPid !== current.pid ||
    (health.managedRuntime as { enabled?: boolean } | undefined)?.enabled !== true) {
    throw new Error('原后台服务尚未响应，已保留其任务。请稍后重新打开客户端。')
  }
  const executable = await fs.stat(executablePath)
  const attached = await controlBackendRuntime(current, clientId, 'attach')
  if (attached.state !== 'open') { await waitBackendExit(current.pid); return undefined }
  if (current.executablePath !== executablePath || current.executableMtimeMs !== executable.mtimeMs) {
    try { await retireBackendRuntime(current, clientId, async () => {}) }
    finally { await controlBackendRuntime(current, clientId, 'detach').catch(() => undefined) }
    return undefined
  }
  return current
}

/** 原子登记后端接入位置和私有控制随机数，不复制应用业务状态。 */
export async function writeBackendRuntime(file: string, runtime: BackendRuntime): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true })
  const temporary = `${file}.${process.pid}.tmp`
  await fs.writeFile(temporary, JSON.stringify(runtime), { mode: 0o600 })
  await fs.rename(temporary, file)
}

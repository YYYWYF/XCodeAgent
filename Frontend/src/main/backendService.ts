import { app } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs/promises'
import net from 'node:net'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { APP_ENV, DEVAGENTSTUDIO_ENV } from './env'
import { backendEnvFilename, checkBackendHealth } from './backendEnvironment'
import { readBackendHealth } from './backendEnvironment'
import { readReusableBackend, writeBackendRuntime, type BackendRuntime } from './backendRuntime'
import { controlBackendRuntime, retireBackendRuntime } from './backendRuntimeClient'
import { BackendConnectionMonitor } from './backendConnectionMonitor'
import { stopStartingBackend } from './backendProcess'

const BACKEND_EXECUTABLE_NAMES: Partial<Record<NodeJS.Platform, string>> = {
  darwin: 'devagentstudio-backend',
  win32: 'devagentstudio-backend.exe'
}
const BACKEND_HOST = '127.0.0.1'
const HEALTH_TIMEOUT_MS = 30000
const HEALTH_POLL_INTERVAL_MS = 500

let backendProcess: ChildProcess | null = null
let backendBaseUrl = DEVAGENTSTUDIO_ENV.DEVAGENTSTUDIO_BACKEND_URL
let stoppingBackend = false
let activeRuntime: BackendRuntime | undefined
let leaseTimer: ReturnType<typeof setTimeout> | undefined
let leaseGeneration = 0
let serviceEpoch = 0
let startupPromise: Promise<string> | undefined
const clientId = randomUUID()

export const backendConnectionMonitor = new BackendConnectionMonitor({
  baseUrl: backendBaseUrl,
  probe: probeBackendConnection,
  recover: async () => {
    await startBackendService()
    return { baseUrl: backendBaseUrl, backendInstanceId: backendConnectionMonitor.getState().backendInstanceId }
  }
})

type BundledBackendPaths = {
  backendDir: string
  executablePath: string
  envFilePath: string
}

/** 返回本次启动选择的后端地址。 */
export function getBackendBaseUrl(): string {
  return backendBaseUrl
}

/** 启动内置后端或核验源码后端，并在环境一致后放行。 */
export async function startBackendService(): Promise<string> {
  if (startupPromise) return startupPromise
  const epoch = serviceEpoch
  const pending = performBackendStart(epoch).then(async (baseUrl) => {
    if (epoch !== serviceEpoch) throw new Error('后台连接操作已取消。')
    const health = await readBackendHealth(baseUrl, APP_ENV)
    if (epoch !== serviceEpoch) throw new Error('后台连接操作已取消。')
    if (!health) throw new Error('后台尚未就绪，请稍后重新同步。')
    backendConnectionMonitor.ready({ baseUrl, backendInstanceId: String(health.backendInstanceId || '') })
    backendConnectionMonitor.start()
    return baseUrl
  }).finally(() => { if (startupPromise === pending) startupPromise = undefined })
  startupPromise = pending
  return pending
}

/** 只在原 PID 已退出或登记确认不存在时启动后继，活着但失联的后台始终保留。 */
async function performBackendStart(epoch: number): Promise<string> {
  if (!shouldStartBundledBackend()) {
    backendBaseUrl = DEVAGENTSTUDIO_ENV.DEVAGENTSTUDIO_BACKEND_URL
    await waitForBackendHealth(backendBaseUrl)
    return backendBaseUrl
  }

  if (activeRuntime && await probeBackendConnection()) return backendBaseUrl
  activeRuntime = undefined

  const bundledBackend = await resolveBundledBackendPaths()
  const runtimeFile = path.join(app.getPath('userData'), 'backend-runtime.json')
  const reusable = await readReusableBackend(runtimeFile, APP_ENV, bundledBackend.executablePath, clientId)
  if (epoch !== serviceEpoch) {
    if (reusable) await controlBackendRuntime(reusable, clientId, 'detach').catch(() => undefined)
    throw new Error('后台连接操作已取消。')
  }
  if (reusable) { backendBaseUrl = reusable.baseUrl; connectRuntime(reusable); return backendBaseUrl }
  const port = await findAvailablePort()
  if (epoch !== serviceEpoch) throw new Error('后台连接操作已取消。')
  backendBaseUrl = `http://${BACKEND_HOST}:${port}`
  const controlToken = randomUUID() + randomUUID()
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    APP_ENV,
    DEVAGENTSTUDIO_BACKEND_HOST: BACKEND_HOST,
    DEVAGENTSTUDIO_BACKEND_PORT: String(port),
    DEVAGENTSTUDIO_BACKEND_ENV_FILE: bundledBackend.envFilePath,
    DEVAGENTSTUDIO_BACKEND_CONTROL_TOKEN: controlToken,
    DEVAGENTSTUDIO_WORKING_DIR: DEVAGENTSTUDIO_ENV.WORKING_DIR,
    PYTHONUTF8: '1'
  }

  const child = spawn(bundledBackend.executablePath, [], {
    cwd: bundledBackend.backendDir,
    env,
    // 后端已自行保存平台日志；不能使用随 Electron 消失的管道承载常驻服务输出。
    stdio: 'ignore',
    windowsHide: true,
    detached: true
  })

  backendProcess = child
  wireBackendLogging(child)
  child.unref()

  try {
    await waitForBackendHealth(backendBaseUrl, child)
    const health = await readBackendHealth(backendBaseUrl, APP_ENV)
    if (!child.pid || typeof health?.backendInstanceId !== 'string' || health.backendPid !== child.pid ||
      (health.managedRuntime as { enabled?: boolean } | undefined)?.enabled !== true) throw new Error('后端未返回可接管的实例身份。')
    const runtime: BackendRuntime = { pid: child.pid, baseUrl: backendBaseUrl,
      backendInstanceId: health.backendInstanceId, appEnv: APP_ENV, executablePath: bundledBackend.executablePath,
      executableMtimeMs: (await fs.stat(bundledBackend.executablePath)).mtimeMs, controlToken }
    if ((await controlBackendRuntime(runtime, clientId, 'attach')).state !== 'open') throw new Error('后台正在退出，请重新打开客户端。')
    await writeBackendRuntime(runtimeFile, runtime)
    if (epoch !== serviceEpoch) {
      await controlBackendRuntime(runtime, clientId, 'detach').catch(() => undefined)
      throw new Error('后台连接操作已取消。')
    }
    connectRuntime(runtime)
    child.unref()
  } catch (error) {
    await stopBackendService()
    throw error
  }

  return backendBaseUrl
}

/** 启动失败只回收本次子进程，监督重连不使用此函数强杀原后台。 */
async function stopBackendService(): Promise<void> {
  const child = backendProcess
  if (!child) return

  backendProcess = null
  stoppingBackend = true

  try {
    await stopStartingBackend(child)
  } finally {
    stoppingBackend = false
  }
}

/** 前端退出只释放租约，业务任务继续执行；最后一个任务收口后后台自行退场。 */
export async function detachBackendService(): Promise<void> {
  serviceEpoch++
  leaseGeneration++
  clearTimeout(leaseTimer)
  backendConnectionMonitor.stop()
  const runtime = activeRuntime
  activeRuntime = undefined
  if (runtime) await controlBackendRuntime(runtime, clientId, 'detach').catch(() => undefined)
  backendProcess?.unref()
  backendProcess = null
}

/** 安装器接管前关闭空闲后台并等待原 PID 退出；忙碌或清理失败时保留服务。 */
export async function prepareBackendServiceUpdate(cleanup: () => Promise<void>): Promise<void> {
  if (startupPromise) throw new Error('后台正在重新连接，请同步完成后再更新。')
  serviceEpoch++
  leaseGeneration++
  clearTimeout(leaseTimer)
  backendConnectionMonitor.stop()
  const runtime = activeRuntime
  if (!runtime) { await cleanup(); return }
  try {
    await retireBackendRuntime(runtime, clientId, cleanup)
    activeRuntime = undefined
    backendProcess = null
  } catch (error) {
    // 提交退出后回执丢失时不假装服务仍可用，下一次启动必须重新核验实例。
    const health = await readBackendHealth(runtime.baseUrl, APP_ENV).catch(() => undefined)
    if (!health || (health.managedRuntime as { phase?: string } | undefined)?.phase === 'exiting') {
      activeRuntime = undefined
      backendProcess = null
    }
    backendConnectionMonitor.start()
    if (activeRuntime) connectRuntime(activeRuntime)
    throw error
  }
}

/** 仅为内置后台续租；失败静默保留原实例，不探测健康、不广播断连、不自动启动。 */
function connectRuntime(runtime: BackendRuntime): void {
  activeRuntime = runtime
  clearTimeout(leaseTimer)
  const generation = ++leaseGeneration
  /** 租约只维持后台生命周期；通信状态由实际业务请求感知。 */
  const renew = async (): Promise<void> => {
    if (activeRuntime !== runtime || generation !== leaseGeneration) return
    try { await controlBackendRuntime(runtime, clientId, 'attach') }
    catch { /* 下次正常请求负责呈现通信失败，租约不产生界面错误。 */ }
    if (activeRuntime === runtime && generation === leaseGeneration) schedule()
  }
  /** 串行安排续租，不与健康检查或 Renderer 状态同步耦合。 */
  const schedule = (): void => {
    leaseTimer = setTimeout(() => void renew(), 15000)
    leaseTimer.unref()
  }
  schedule()
}

/** 启动或明确进程事件中核验环境与实例，不用于定时在线检测。 */
async function probeBackendConnection(): Promise<{ baseUrl: string; backendInstanceId: string } | undefined> {
  const runtime = activeRuntime
  if (shouldStartBundledBackend() && !runtime) return undefined
  const health = await readBackendHealth(runtime?.baseUrl || backendBaseUrl, APP_ENV)
  if (!health || (runtime && (health.backendInstanceId !== runtime.backendInstanceId || health.backendPid !== runtime.pid ||
    (health.managedRuntime as { phase?: string } | undefined)?.phase !== 'open'))) return undefined
  return { baseUrl: runtime?.baseUrl || backendBaseUrl, backendInstanceId: String(health.backendInstanceId || '') }
}

function shouldStartBundledBackend(): boolean {
  return app.isPackaged && Boolean(BACKEND_EXECUTABLE_NAMES[process.platform])
}

/** 校验冻结后端资源及唯一环境文件，防止安装包夹带其他环境配置。 */
async function resolveBundledBackendPaths(): Promise<BundledBackendPaths> {
  const backendDir = path.join(process.resourcesPath, 'backend')
  const executableName = BACKEND_EXECUTABLE_NAMES[process.platform]
  if (!executableName) {
    throw new Error(`Bundled backend is not supported on ${process.platform}`)
  }

  const executablePath = path.join(backendDir, executableName)
  const envFilePath = path.join(backendDir, backendEnvFilename(APP_ENV))
  const unexpectedFiles = (await fs.readdir(backendDir)).filter(
    (name) => /^\.env(?:\..*)?$/i.test(name) && name !== backendEnvFilename(APP_ENV)
  )
  if (unexpectedFiles.length > 0) {
    throw new Error(
      `Packaged backend contains unexpected environment files: ${unexpectedFiles.join(', ')}`
    )
  }

  await assertFileExists(executablePath, 'Packaged backend executable')
  await assertFileExists(envFilePath, `Packaged backend ${APP_ENV} configuration`)

  const bundledSkillsDir = path.join(backendDir, '_internal', 'app', 'builtin_skills')
  await assertDirectoryExists(bundledSkillsDir, 'Packaged backend built-in skills directory')

  return {
    backendDir,
    executablePath,
    envFilePath
  }
}

async function assertFileExists(filePath: string, label: string): Promise<void> {
  try {
    const stat = await fs.stat(filePath)
    if (stat.isFile()) return
  } catch {
    // The clearer error below includes the packaged path Electron tried to use.
  }
  throw new Error(`${label} not found: ${filePath}`)
}

async function assertDirectoryExists(directoryPath: string, label: string): Promise<void> {
  try {
    const stat = await fs.stat(directoryPath)
    if (stat.isDirectory()) return
  } catch {
    // The clearer error below includes the packaged path Electron tried to use.
  }
  throw new Error(`${label} not found: ${directoryPath}`)
}

function wireBackendLogging(child: ChildProcess): void {
  child.stdout?.on('data', (chunk) => {
    console.log(`[backend] ${String(chunk).trimEnd()}`)
  })
  child.stderr?.on('data', (chunk) => {
    console.error(`[backend] ${String(chunk).trimEnd()}`)
  })
  child.once('exit', (code, signal) => {
    if (backendProcess === child) {
      backendProcess = null
    }
    if (!stoppingBackend) {
      console.error(
        `Backend process exited unexpectedly: code=${code ?? 'null'} signal=${signal ?? 'null'}`
      )
      void backendConnectionMonitor.checkNow()
    }
  })
}

async function findAvailablePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, BACKEND_HOST, () => {
      const address = server.address()
      server.close(() => {
        if (address && typeof address === 'object') {
          resolve(address.port)
          return
        }
        reject(new Error('Failed to allocate backend port'))
      })
    })
  })
}

/** 在既有就绪预算内等待服务，环境错配立即失败。 */
async function waitForBackendHealth(baseUrl: string, child?: ChildProcess): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) {
      throw new Error(`Backend exited before becoming healthy: code=${child.exitCode}`)
    }
    if (await checkBackendHealth(baseUrl, APP_ENV)) {
      return
    }
    await delay(HEALTH_POLL_INTERVAL_MS)
  }

  throw new Error(`Backend did not become healthy within ${HEALTH_TIMEOUT_MS}ms: ${baseUrl}/health`)
}

/** 在既有就绪预算中等待下一轮健康探测。 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

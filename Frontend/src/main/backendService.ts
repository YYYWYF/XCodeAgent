import { app } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs/promises'
import net from 'node:net'
import path from 'node:path'
import { APP_ENV, DEVAGENTSTUDIO_ENV } from './env'
import { backendEnvFilename, checkBackendHealth } from './backendEnvironment'
import { stopBackendProcess } from './backendProcess'

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
let serviceEpoch = 0
let startupPromise: Promise<string> | undefined

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
  if (stoppingBackend) throw new Error('后台服务正在停止。')
  if (startupPromise) return startupPromise
  const epoch = serviceEpoch
  const pending = performBackendStart(epoch)
    .then((baseUrl) => {
      if (epoch !== serviceEpoch) throw new Error('后台启动操作已取消。')
      return baseUrl
    })
    .finally(() => {
      if (startupPromise === pending) startupPromise = undefined
    })
  startupPromise = pending
  return pending
}

/** 内置后台只由当前 Electron 进程持有，不读取或接管上次退出后留下的实例。 */
async function performBackendStart(epoch: number): Promise<string> {
  if (!shouldStartBundledBackend()) {
    backendBaseUrl = DEVAGENTSTUDIO_ENV.DEVAGENTSTUDIO_BACKEND_URL
    await waitForBackendHealth(backendBaseUrl)
    return backendBaseUrl
  }

  if (backendProcess && backendProcess.exitCode === null && backendProcess.signalCode === null) {
    await waitForBackendHealth(backendBaseUrl, backendProcess)
    return backendBaseUrl
  }

  const bundledBackend = await resolveBundledBackendPaths()
  const port = await findAvailablePort()
  if (epoch !== serviceEpoch) throw new Error('后台连接操作已取消。')
  backendBaseUrl = `http://${BACKEND_HOST}:${port}`
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    APP_ENV,
    DEVAGENTSTUDIO_BACKEND_HOST: BACKEND_HOST,
    DEVAGENTSTUDIO_BACKEND_PORT: String(port),
    DEVAGENTSTUDIO_BACKEND_ENV_FILE: bundledBackend.envFilePath,
    DEVAGENTSTUDIO_WORKING_DIR: DEVAGENTSTUDIO_ENV.WORKING_DIR,
    PYTHONUTF8: '1'
  }

  const child = spawn(bundledBackend.executablePath, [], {
    cwd: bundledBackend.backendDir,
    env,
    // 后台生命周期跟随当前客户端，恢复管道以便记录启动阶段的输出和错误。
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: process.platform !== 'win32'
  })

  backendProcess = child
  wireBackendLogging(child)

  try {
    await waitForBackendHealth(backendBaseUrl, child)
    if (epoch !== serviceEpoch) throw new Error('后台连接操作已取消。')
  } catch (error) {
    // 正常退出已接管回收责任；启动自身失败时只清理本次创建的进程。
    if (epoch === serviceEpoch) {
      stoppingBackend = true
      try {
        await stopBackendProcess(child)
      } finally {
        stoppingBackend = false
      }
    }
    throw error
  }

  return backendBaseUrl
}

/** 客户端退出或安装更新时停止其内置后台，并等待在途启动收口。 */
export async function stopBackendService(): Promise<void> {
  serviceEpoch++
  const pending = startupPromise
  const child = backendProcess
  stoppingBackend = true

  try {
    if (child) await stopBackendProcess(child)
    // 尚在解析资源或等待就绪的启动必须观察到代次失效，避免客户端退出后补启动。
    await pending?.catch(() => undefined)
  } finally {
    if (backendProcess === child && child && (child.exitCode !== null || child.signalCode !== null))
      backendProcess = null
    stoppingBackend = false
  }
}

/** 安装器接管前先清理应用预览，再停止当前客户端持有的内置后台。 */
export async function prepareBackendServiceUpdate(cleanup: () => Promise<void>): Promise<void> {
  await cleanup()
  await stopBackendService()
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
    if (child && (child.exitCode !== null || child.signalCode !== null)) {
      throw new Error(
        `Backend exited before becoming healthy: code=${child.exitCode} signal=${child.signalCode}`
      )
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

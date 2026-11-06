import { app } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs/promises'
import net from 'node:net'
import path from 'node:path'
import { APP_ENV, DEVAGENTSTUDIO_ENV } from './env'
import { backendEnvFilename, checkBackendHealth } from './backendEnvironment'

const BACKEND_EXECUTABLE_NAMES: Partial<Record<NodeJS.Platform, string>> = {
  darwin: 'devagentstudio-backend',
  win32: 'devagentstudio-backend.exe'
}
const BACKEND_HOST = '127.0.0.1'
const HEALTH_TIMEOUT_MS = 30000
const HEALTH_POLL_INTERVAL_MS = 500
const SHUTDOWN_TIMEOUT_MS = 5000

let backendProcess: ChildProcess | null = null
let backendBaseUrl = DEVAGENTSTUDIO_ENV.DEVAGENTSTUDIO_BACKEND_URL
let stoppingBackend = false

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
  if (!shouldStartBundledBackend()) {
    backendBaseUrl = DEVAGENTSTUDIO_ENV.DEVAGENTSTUDIO_BACKEND_URL
    await waitForBackendHealth(backendBaseUrl)
    return backendBaseUrl
  }

  if (backendProcess) {
    return backendBaseUrl
  }

  const port = await findAvailablePort()
  backendBaseUrl = `http://${BACKEND_HOST}:${port}`
  const bundledBackend = await resolveBundledBackendPaths()
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
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: process.platform !== 'win32'
  })

  backendProcess = child
  wireBackendLogging(child)

  try {
    await waitForBackendHealth(backendBaseUrl, child)
  } catch (error) {
    await stopBackendService()
    throw error
  }

  return backendBaseUrl
}

export async function stopBackendService(): Promise<void> {
  const child = backendProcess
  if (!child) return

  backendProcess = null
  stoppingBackend = true

  try {
    if (child.exitCode !== null || child.killed) return

    if (process.platform === 'win32') {
      const killedTree = await killWindowsProcessTree(child)
      if (!killedTree && child.exitCode === null) {
        child.kill()
        await waitForProcessExit(child, SHUTDOWN_TIMEOUT_MS)
      }
      return
    }

    signalPosixProcessTree(child, 'SIGTERM')
    const exited = await waitForProcessExit(child, SHUTDOWN_TIMEOUT_MS)
    if (!exited && child.exitCode === null) {
      signalPosixProcessTree(child, 'SIGKILL')
      await waitForProcessExit(child, SHUTDOWN_TIMEOUT_MS)
    }
  } finally {
    stoppingBackend = false
  }
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

/** 使用 taskkill 终止 Windows 子进程树，并返回命令是否成功。 */
function killWindowsProcessTree(child: ChildProcess): Promise<boolean> {
  return new Promise((resolve) => {
    if (!child.pid) {
      resolve(false)
      return
    }

    const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true
    })
    killer.once('exit', (code) => {
      resolve(code === 0)
    })
    killer.once('error', () => {
      resolve(false)
    })
  })
}

/** 向 macOS/POSIX 独立进程组发送信号，组不存在时回退到直接子进程。 */
function signalPosixProcessTree(child: ChildProcess, signal: NodeJS.Signals): void {
  if (!child.pid) return
  try {
    process.kill(-child.pid, signal)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') {
      child.kill(signal)
    }
  }
}

function waitForProcessExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) {
    return Promise.resolve(true)
  }

  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      child.off('exit', onExit)
      resolve(false)
    }, timeoutMs)

    const onExit = (): void => {
      clearTimeout(timeout)
      resolve(true)
    }

    child.once('exit', onExit)
  })
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

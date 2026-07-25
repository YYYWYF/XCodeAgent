import { app } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs/promises'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { XCODE_AGENT_ENV } from './env'

const BACKEND_EXECUTABLE_NAMES: Partial<Record<NodeJS.Platform, string>> = {
  darwin: 'xcodeagent-backend',
  win32: 'xcodeagent-backend.exe'
}
const BACKEND_HOST = '127.0.0.1'
const HEALTH_TIMEOUT_MS = 30000
const HEALTH_POLL_INTERVAL_MS = 500
const SHUTDOWN_TIMEOUT_MS = 5000

let backendProcess: ChildProcess | null = null
let backendBaseUrl = XCODE_AGENT_ENV.XCODE_AGENT_BACKEND_URL
let stoppingBackend = false

type BundledBackendPaths = {
  backendDir: string
  executablePath: string
  envFilePath: string
}

export function getBackendBaseUrl(): string {
  return backendBaseUrl
}

export async function startBackendService(): Promise<string> {
  if (!shouldStartBundledBackend()) {
    backendBaseUrl = XCODE_AGENT_ENV.XCODE_AGENT_BACKEND_URL
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
    XCODEAGENT_BACKEND_HOST: BACKEND_HOST,
    XCODEAGENT_BACKEND_PORT: String(port),
    XCODEAGENT_BACKEND_ENV_FILE: bundledBackend.envFilePath,
    XCODEAGENT_WORKING_DIR: XCODE_AGENT_ENV.WORKING_DIR,
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

async function resolveBundledBackendPaths(): Promise<BundledBackendPaths> {
  const backendDir = path.join(process.resourcesPath, 'backend')
  const executableName = BACKEND_EXECUTABLE_NAMES[process.platform]
  if (!executableName) {
    throw new Error(`Bundled backend is not supported on ${process.platform}`)
  }

  const executablePath = path.join(backendDir, executableName)
  const envFilePath = path.join(backendDir, '.env')

  await assertFileExists(executablePath, 'Packaged backend executable')
  await assertFileExists(envFilePath, 'Packaged backend .env')

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

async function waitForBackendHealth(baseUrl: string, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Backend exited before becoming healthy: code=${child.exitCode}`)
    }
    if (await checkHealth(baseUrl)) {
      return
    }
    await delay(HEALTH_POLL_INTERVAL_MS)
  }

  throw new Error(`Backend did not become healthy within ${HEALTH_TIMEOUT_MS}ms: ${baseUrl}/health`)
}

function checkHealth(baseUrl: string): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (healthy: boolean): void => {
      if (settled) return
      settled = true
      resolve(healthy)
    }

    const request = http.get(`${baseUrl}/health`, { timeout: 1000 }, (response) => {
      response.resume()
      finish(
        Boolean(response.statusCode && response.statusCode >= 200 && response.statusCode < 300)
      )
    })

    request.once('timeout', () => {
      request.destroy()
      finish(false)
    })
    request.once('error', () => {
      finish(false)
    })
  })
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

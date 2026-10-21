import { app, shell } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { DEVAGENTSTUDIO_ENV } from './env'
import { getBackendBaseUrl } from './backendService'
import { runAuthentication } from './authClient'
import type { AuthenticationProgress, CloudJsonRequest, AuthenticationResult } from '../shared/authentication'

const sessionId = crypto.randomUUID()
let loginPending = false
let quitting = false
let authenticationInitialized = false

/** 获取当前运行环境的数据目录，不缓存任何用户 token。 */
export function getDevAgentStudioDataDir(): string {
  return path.join(app.getPath('home'), DEVAGENTSTUDIO_ENV.WORKING_DIR)
}

/** 按需创建环境数据目录。 */
export async function ensureDevAgentStudioDataDir(): Promise<string> {
  const directory = getDevAgentStudioDataDir()
  await fs.mkdir(directory, { recursive: true })
  return directory
}

/** 通过标准 AG-UI 动作调用 Python 认证业务。 */
async function action(name: string, extra: Record<string, unknown> = {}): Promise<AuthenticationResult> {
  return runAuthentication(getBackendBaseUrl(), {
    action: name,
    sessionId,
    workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR,
    ...extra
  })
}

/** 仅在点击登录按钮后建立 Python 会话，启动登录窗口不依赖认证接口。 */
async function initializeAuthentication(): Promise<void> {
  if (authenticationInitialized) return
  await action('initialize')
  authenticationInitialized = true
}

/** 每次从 Python 的凭据文件取得公开登录状态。 */
export async function getAuthenticationStatus(): Promise<AuthenticationResult> {
  // 冷启动已清理凭据；尚未点击登录时直接显示原有登录窗口。
  if (!authenticationInitialized) return { status: 'completed', authenticated: false }
  return action('status')
}

/** 由后端读取文件和有效期，判断是否允许进入主窗口。 */
export async function hasValidAuthToken(): Promise<boolean> {
  return (await getAuthenticationStatus()).authenticated === true
}

/** 调用 Python 云效认证流，主进程只负责系统浏览器和公开进度。 */
export async function loginWithYunxiao(onProgress: (value: AuthenticationProgress) => void): Promise<void> {
  if (quitting) throw new Error('应用正在退出。')
  if (loginPending) throw new Error('已有登录正在进行。')
  loginPending = true
  try {
    await initializeAuthentication()
    if (quitting) throw new Error('应用正在退出。')
    await runAuthentication(getBackendBaseUrl(), {
      action: 'login', sessionId, workingDir: DEVAGENTSTUDIO_ENV.WORKING_DIR
    }, async (value) => {
      if (quitting) throw new Error('应用正在退出。')
      if (value.authorizationUrl) {
        const url = new URL(value.authorizationUrl)
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
          throw new Error('Python 返回了无效授权地址。')
        }
        await shell.openExternal(url.toString())
      }
      if (value.progress) onProgress(value.progress)
    })
    if (quitting || !(await hasValidAuthToken())) throw new Error('登录凭据未保存或已失效。')
  } catch (error) {
    if (!quitting && authenticationInitialized) await action('cancel').catch(() => undefined)
    throw error
  } finally {
    loginPending = false
  }
}

/** 取消后端轮询并清除当前登录凭据，允许用户重新登录。 */
export async function clearAuthState(): Promise<void> {
  if (authenticationInitialized) await action('logout')
  else await revokeLocalCredentials()
}

/** 读取凭据和携带 token 的云端业务请求均由 Python 完成。 */
export async function requestAuthenticatedCloud(input: CloudJsonRequest): Promise<{ httpStatus: number; body: unknown }> {
  const result = await action('request', { request: input })
  if (typeof result.httpStatus !== 'number') throw new Error('云端接口没有返回 HTTP 状态。')
  return { httpStatus: result.httpStatus, body: result.body }
}

/** 退出前使后端会话失效，旧登录任务不得再次保存凭据。 */
export async function shutdownAuthentication(): Promise<void> {
  quitting = true
  try {
    if (authenticationInitialized) await action('shutdown')
  } finally {
    await revokeLocalCredentials()
  }
}

/** 启动和退出时的本地清理兜底，仅写无秘密的栅栏并删除凭据。 */
export async function revokeLocalCredentials(): Promise<void> {
  const directory = path.join(getDevAgentStudioDataDir(), 'yst-login')
  await fs.mkdir(directory, { recursive: true })
  const metadata = await fs.lstat(directory)
  if (metadata.isSymbolicLink()) throw new Error('认证目录不能是符号链接。')
  const fence = path.join(directory, 'session.json')
  if ((await fs.lstat(fence).catch(() => undefined))?.isSymbolicLink()) throw new Error('认证会话文件不能是符号链接。')
  await fs.writeFile(fence, JSON.stringify({ sessionId, active: false }), { mode: 0o600 })
  await fs.unlink(path.join(directory, 'auth.json')).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error
  })
  for (const name of await fs.readdir(directory)) {
    if (name.startsWith('.auth-') && name.endsWith('.tmp')) await fs.unlink(path.join(directory, name))
  }
}

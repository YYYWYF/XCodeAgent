import { app } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { XCODE_AGENT_ENV } from './env'

export type AuthRecord = {
  access_token: string
}

const MOCK_LOGIN_DELAY_MS = 2000

let accessToken: string | null = null

/** 获取当前运行环境对应的 XCodeAgent 数据目录。 */
export function getXcodeAgentDataDir(): string {
  return path.join(app.getPath('home'), XCODE_AGENT_ENV.WORKING_DIR)
}

/** 确保当前运行环境对应的数据目录已经创建。 */
export async function ensureXcodeAgentDataDir(): Promise<string> {
  const dataDir = getXcodeAgentDataDir()
  await fs.mkdir(dataDir, { recursive: true })
  return dataDir
}

/** 等待指定时长，用于保留当前模拟登录的交互节奏。 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

/** 返回当前环境的认证记录文件路径。 */
function getAuthFile(): string {
  return path.join(getXcodeAgentDataDir(), 'auth.json')
}

/** 判断未知数据是否是只包含有效 access_token 的认证记录。 */
function isAuthRecord(value: unknown): value is AuthRecord {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<AuthRecord>
  return typeof candidate.access_token === 'string' && Boolean(candidate.access_token.trim())
}

/** 从当前环境的 auth.json 读取有效认证记录。 */
async function readAuthRecord(): Promise<AuthRecord | null> {
  try {
    const rawValue = await fs.readFile(getAuthFile(), 'utf8')
    const parsedValue = JSON.parse(rawValue || '{}')
    return isAuthRecord(parsedValue) ? parsedValue : null
  } catch {
    return null
  }
}

/** 将认证记录写入当前环境的 auth.json。 */
async function writeAuthRecord(authRecord: AuthRecord): Promise<void> {
  await ensureXcodeAgentDataDir()
  await fs.writeFile(getAuthFile(), `${JSON.stringify(authRecord, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600
  })
}

/** 初始化主进程内存中的认证状态，旧 token 格式不会被接受。 */
export async function initializeAuthState(): Promise<void> {
  const authRecord = await readAuthRecord()
  accessToken = authRecord?.access_token.trim() || null
}

/** 返回主进程内存中是否存在有效 access_token。 */
export function hasValidAuthToken(): boolean {
  return Boolean(accessToken)
}

/** 返回主进程内存中的 access_token，不触碰持久化文件。 */
export function getAccessToken(): string | null {
  return accessToken
}

/** 模拟 CMB Device Flow；后续在此替换为真实认证实现。 */
export async function authCmbDeviceFlow(): Promise<AuthRecord> {
  await delay(MOCK_LOGIN_DELAY_MS)
  return {
    access_token: `mock-access-token-${Date.now()}-${crypto.randomBytes(8).toString('hex')}`
  }
}

/** 执行登录流程，并在落盘成功后更新主进程内存。 */
export async function loginWithCmbDeviceFlow(): Promise<void> {
  const authRecord = await authCmbDeviceFlow()
  await writeAuthRecord(authRecord)
  accessToken = authRecord.access_token
}

/** 清空主进程认证内存并删除当前环境的 auth.json。 */
export async function clearAuthState(): Promise<void> {
  accessToken = null
  try {
    await fs.unlink(getAuthFile())
  } catch (error) {
    const errorCode =
      error && typeof error === 'object' && 'code' in error
        ? (error as { code?: string }).code
        : undefined
    if (errorCode !== 'ENOENT') throw error
  }
}

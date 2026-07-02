import { app } from 'electron'
import { randomUUID } from 'crypto'
import { mkdir, readFile, rm, writeFile } from 'fs/promises'
import { dirname, join } from 'path'

export type LoginResult = {
  token: string
}

type AuthTokenRecord = {
  token: string
  createdAt: string
}

const AUTH_FILE_NAME = 'auth.json'
const MOCK_LOGIN_DELAY_MS = 2000

const wait = (duration: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, duration)
  })

export const getAuthFilePath = (): string => join(app.getPath('userData'), AUTH_FILE_NAME)

export const getStoredToken = async (): Promise<string | null> => {
  try {
    const fileContent = await readFile(getAuthFilePath(), 'utf-8')
    const tokenRecord = JSON.parse(fileContent) as Partial<AuthTokenRecord>

    return typeof tokenRecord.token === 'string' && tokenRecord.token ? tokenRecord.token : null
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.error(error)
    }

    return null
  }
}

const saveAuthToken = async (token: string): Promise<void> => {
  const filePath = getAuthFilePath()
  const tokenRecord: AuthTokenRecord = {
    token,
    createdAt: new Date().toISOString()
  }

  await mkdir(dirname(filePath), { recursive: true })
  await writeFile(filePath, JSON.stringify(tokenRecord), 'utf-8')
}

export const simulateLogin = async (): Promise<LoginResult> => {
  await wait(MOCK_LOGIN_DELAY_MS)

  const token = `mock-token-${randomUUID()}`
  await saveAuthToken(token)

  return { token }
}

export const clearAuthToken = async (): Promise<void> => {
  await rm(getAuthFilePath(), { force: true })
}

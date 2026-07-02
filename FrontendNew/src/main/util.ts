import { spawn } from 'child_process'
import { createWriteStream } from 'fs'
import { mkdtemp, mkdir, rm } from 'fs/promises'
import { tmpdir } from 'os'
import { join, resolve } from 'path'
import { pipeline } from 'stream/promises'
import { backendHttp } from './http'
import type {
  DownloadZipAndExtractInput,
  DownloadZipAndExtractResult,
  DownloadZipRequestParams
} from '../shared/api'

type CommandResult = {
  stdout: string
  stderr: string
}

const DOWNLOAD_PATH = '/download'
const TEMP_DIR_PREFIX = 'xcodeagent-download-'
const TEMP_ZIP_FILE_NAME = 'download.zip'
const FORCE_KILL_DELAY_MS = 5_000

const requireNonEmptyString = (value: string, fieldName: string): string => {
  const trimmedValue = value.trim()

  if (!trimmedValue) {
    throw new Error(`${fieldName} must be a non-empty string`)
  }

  return trimmedValue
}

const normalizeTimeoutMs = (timeoutMs: number | undefined): number | undefined => {
  if (timeoutMs === undefined) {
    return undefined
  }

  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) {
    throw new Error('timeoutMs must be a positive integer')
  }

  return timeoutMs
}

const resolveTargetDir = (targetDir: string): string =>
  resolve(requireNonEmptyString(targetDir, 'targetDir'))

const isReadableStream = (value: unknown): value is NodeJS.ReadableStream =>
  typeof value === 'object' && value !== null && 'pipe' in value && typeof value.pipe === 'function'

const runCommand = async (input: {
  command: string
  args: string[]
  timeoutMs?: number
}): Promise<CommandResult> =>
  new Promise((resolvePromise, rejectPromise) => {
    const childProcess = spawn(input.command, input.args, {
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    const stdoutChunks: Buffer[] = []
    const stderrChunks: Buffer[] = []
    let timedOut = false
    let timeout: NodeJS.Timeout | undefined
    let forceKillTimeout: NodeJS.Timeout | undefined
    let settled = false

    const clearTimers = (): void => {
      if (timeout) {
        clearTimeout(timeout)
      }

      if (forceKillTimeout) {
        clearTimeout(forceKillTimeout)
      }
    }

    const settle = (error?: Error, result?: CommandResult): void => {
      if (settled) {
        return
      }

      settled = true
      clearTimers()

      if (error) {
        rejectPromise(error)
        return
      }

      resolvePromise(result ?? { stdout: '', stderr: '' })
    }

    if (input.timeoutMs !== undefined) {
      timeout = setTimeout(() => {
        timedOut = true
        childProcess.kill('SIGTERM')
        forceKillTimeout = setTimeout(() => childProcess.kill('SIGKILL'), FORCE_KILL_DELAY_MS)
      }, input.timeoutMs)
    }

    childProcess.stdout.on('data', (chunk: Buffer) => {
      stdoutChunks.push(chunk)
    })

    childProcess.stderr.on('data', (chunk: Buffer) => {
      stderrChunks.push(chunk)
    })

    childProcess.once('error', (error) => {
      settle(new Error(`Failed to start ${input.command}: ${error.message}`))
    })

    childProcess.once('close', (exitCode, signal) => {
      const stdout = Buffer.concat(stdoutChunks).toString('utf-8')
      const stderr = Buffer.concat(stderrChunks).toString('utf-8')

      if (timedOut) {
        settle(new Error(`${input.command} timed out after ${input.timeoutMs}ms`))
        return
      }

      if (exitCode !== 0) {
        const exitReason = signal ? `signal ${signal}` : `exit code ${exitCode ?? 'unknown'}`
        const message =
          stderr.trim() || stdout.trim() || `${input.command} failed with ${exitReason}`

        settle(new Error(message))
        return
      }

      settle(undefined, { stdout, stderr })
    })
  })

const downloadZipFile = async (input: {
  zipFilePath: string
  params?: DownloadZipRequestParams
  timeoutMs?: number
}): Promise<void> => {
  const response = await backendHttp.get<NodeJS.ReadableStream>(DOWNLOAD_PATH, {
    params: input.params,
    responseType: 'stream',
    timeout: input.timeoutMs
  })

  if (!isReadableStream(response.data)) {
    throw new Error('Download response is not a readable stream')
  }

  await pipeline(response.data, createWriteStream(input.zipFilePath))
}

const listZipEntries = async (zipFilePath: string, timeoutMs?: number): Promise<string[]> => {
  const result = await runCommand({
    command: 'unzip',
    args: ['-Z1', zipFilePath],
    timeoutMs
  })

  return result.stdout
    .split(/\r\n|\n|\r/)
    .map((entry) => entry.trim())
    .filter(Boolean)
}

const extractZipFile = async (input: {
  zipFilePath: string
  targetDir: string
  overwrite: boolean
  timeoutMs?: number
}): Promise<void> => {
  await runCommand({
    command: 'unzip',
    args: [input.overwrite ? '-oq' : '-nq', input.zipFilePath, '-d', input.targetDir],
    timeoutMs: input.timeoutMs
  })
}

export const downloadZipAndExtract = async (
  input: DownloadZipAndExtractInput
): Promise<DownloadZipAndExtractResult> => {
  const targetDir = resolveTargetDir(input.targetDir)
  const timeoutMs = normalizeTimeoutMs(input.timeoutMs)
  const tempDir = await mkdtemp(join(tmpdir(), TEMP_DIR_PREFIX))
  const zipFilePath = join(tempDir, TEMP_ZIP_FILE_NAME)

  try {
    await mkdir(targetDir, { recursive: true })
    await downloadZipFile({
      zipFilePath,
      params: input.params,
      timeoutMs
    })

    const entries = await listZipEntries(zipFilePath, timeoutMs)

    await extractZipFile({
      zipFilePath,
      targetDir,
      overwrite: input.overwrite ?? false,
      timeoutMs
    })

    return {
      targetDir,
      entryCount: entries.length
    }
  } finally {
    await rm(tempDir, { recursive: true, force: true })
  }
}

import { spawn } from 'child_process'
import { access, stat } from 'fs/promises'
import { isAbsolute, relative, resolve } from 'path'

export type CloneGitRepositoryInput = {
  repoUrl: string
  workspaceDir: string
  branch?: string
  directoryName?: string
  depth?: number
  timeoutMs?: number
}

export type CloneGitRepositoryResult = {
  repoUrl: string
  branch?: string
  targetDir: string
}

const FORCE_KILL_DELAY_MS = 5_000

const requireNonEmptyString = (value: string, fieldName: string): string => {
  const trimmedValue = value.trim()

  if (!trimmedValue) {
    throw new Error(`${fieldName} must be a non-empty string`)
  }

  return trimmedValue
}

const normalizeDirectoryName = (directoryName: string): string => {
  const normalizedDirectoryName = requireNonEmptyString(directoryName, 'directoryName')

  if (
    normalizedDirectoryName === '.' ||
    normalizedDirectoryName === '..' ||
    isAbsolute(normalizedDirectoryName) ||
    normalizedDirectoryName.includes('/') ||
    normalizedDirectoryName.includes('\\') ||
    normalizedDirectoryName.includes('\0')
  ) {
    throw new Error(`Invalid git clone directory name: ${directoryName}`)
  }

  return normalizedDirectoryName
}

const deriveDirectoryNameFromRepoUrl = (repoUrl: string): string => {
  const normalizedRepoUrl = repoUrl
    .replace(/[?#].*$/, '')
    .replace(/\/+$/, '')
    .trim()
  const lastSegment = normalizedRepoUrl
    .split(/[/:\\]/)
    .filter(Boolean)
    .pop()

  if (!lastSegment) {
    throw new Error(`Unable to infer git clone directory name from repoUrl: ${repoUrl}`)
  }

  const directoryName = lastSegment.endsWith('.git') ? lastSegment.slice(0, -4) : lastSegment

  return normalizeDirectoryName(directoryName)
}

const assertWorkspaceDir = async (workspaceDir: string): Promise<string> => {
  const resolvedWorkspaceDir = resolve(requireNonEmptyString(workspaceDir, 'workspaceDir'))
  const workspaceStat = await stat(resolvedWorkspaceDir)

  if (!workspaceStat.isDirectory()) {
    throw new Error(`workspaceDir is not a directory: ${resolvedWorkspaceDir}`)
  }

  return resolvedWorkspaceDir
}

const resolveTargetDir = (workspaceDir: string, directoryName: string): string => {
  const targetDir = resolve(workspaceDir, directoryName)
  const relativeTargetDir = relative(workspaceDir, targetDir)

  if (!relativeTargetDir || relativeTargetDir.startsWith('..') || isAbsolute(relativeTargetDir)) {
    throw new Error(`Git clone target escapes workspaceDir: ${targetDir}`)
  }

  return targetDir
}

const assertTargetDirDoesNotExist = async (targetDir: string): Promise<void> => {
  try {
    await access(targetDir)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return
    }

    throw error
  }

  throw new Error(`Git clone target directory already exists: ${targetDir}`)
}

const normalizeOptionalString = (
  value: string | undefined,
  fieldName: string
): string | undefined => {
  if (value === undefined) {
    return undefined
  }

  return requireNonEmptyString(value, fieldName)
}

const normalizeDepth = (depth: number | undefined): number | undefined => {
  if (depth === undefined) {
    return undefined
  }

  if (!Number.isInteger(depth) || depth <= 0) {
    throw new Error('depth must be a positive integer')
  }

  return depth
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

const buildGitCloneArgs = (input: {
  repoUrl: string
  targetDir: string
  branch?: string
  depth?: number
}): string[] => {
  const args = ['clone', '--progress']

  if (input.branch) {
    args.push('--branch', input.branch)
  }

  if (input.depth !== undefined) {
    args.push('--depth', String(input.depth))
  }

  args.push(input.repoUrl, input.targetDir)

  return args
}

const createCloneError = (exitCode: number | null, signal: NodeJS.Signals | null): Error => {
  const exitReason = signal ? `signal ${signal}` : `exit code ${exitCode ?? 'unknown'}`

  return new Error(`Git clone failed with ${exitReason}.`)
}

const runGitClone = async (input: {
  args: string[]
  cwd: string
  timeoutMs?: number
}): Promise<void> =>
  new Promise((resolvePromise, rejectPromise) => {
    const childProcess = spawn('git', input.args, {
      cwd: input.cwd,
      shell: false,
      stdio: 'ignore'
    })
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

    const settle = (error?: Error): void => {
      if (settled) {
        return
      }

      settled = true
      clearTimers()

      if (error) {
        rejectPromise(error)
        return
      }

      resolvePromise()
    }

    if (input.timeoutMs !== undefined) {
      timeout = setTimeout(() => {
        timedOut = true
        childProcess.kill('SIGTERM')
        forceKillTimeout = setTimeout(() => childProcess.kill('SIGKILL'), FORCE_KILL_DELAY_MS)
      }, input.timeoutMs)
    }

    childProcess.once('error', (error) => {
      settle(new Error(`Failed to start git clone: ${error.message}`))
    })

    childProcess.once('close', (exitCode, signal) => {
      if (timedOut) {
        settle(new Error(`Git clone timed out after ${input.timeoutMs}ms`))
        return
      }

      if (exitCode !== 0) {
        settle(createCloneError(exitCode, signal))
        return
      }

      settle()
    })
  })

export const cloneGitRepository = async (
  input: CloneGitRepositoryInput
): Promise<CloneGitRepositoryResult> => {
  const repoUrl = requireNonEmptyString(input.repoUrl, 'repoUrl')
  const workspaceDir = await assertWorkspaceDir(input.workspaceDir)
  const branch = normalizeOptionalString(input.branch, 'branch')
  const depth = normalizeDepth(input.depth)
  const timeoutMs = normalizeTimeoutMs(input.timeoutMs)
  const directoryName =
    input.directoryName !== undefined
      ? normalizeDirectoryName(input.directoryName)
      : deriveDirectoryNameFromRepoUrl(repoUrl)
  const targetDir = resolveTargetDir(workspaceDir, directoryName)

  await assertTargetDirDoesNotExist(targetDir)
  await runGitClone({
    args: buildGitCloneArgs({ repoUrl, targetDir, branch, depth }),
    cwd: workspaceDir,
    timeoutMs
  })

  return {
    repoUrl,
    branch,
    targetDir
  }
}

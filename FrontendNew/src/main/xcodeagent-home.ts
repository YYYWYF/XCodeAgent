import { app } from 'electron'
import { mkdir, readFile, stat, writeFile } from 'fs/promises'
import { join } from 'path'
import { DEFAULT_AGENTS_MD, DEFAULT_MEMORY_MD } from './default-agents'
import type {
  AgentsFileContent,
  XcodeAgentMarkdownFileContent,
  XcodeAgentMarkdownFileName,
  XcodeAgentMarkdownFileSummary
} from '../shared/xcodeagent'

const XCODEAGENT_HOME_DIR_NAME = '.xcodeagent'
const AGENTS_FILE_NAME = 'AGENTS.md'
const MEMORY_FILE_NAME = 'MEMORY.md'

const markdownFiles: Array<{
  name: XcodeAgentMarkdownFileName
  enabled: boolean
  defaultContent: string
}> = [
  {
    name: AGENTS_FILE_NAME,
    enabled: true,
    defaultContent: DEFAULT_AGENTS_MD
  },
  {
    name: MEMORY_FILE_NAME,
    enabled: false,
    defaultContent: DEFAULT_MEMORY_MD
  }
]

const isXcodeAgentMarkdownFileName = (value: string): value is XcodeAgentMarkdownFileName =>
  markdownFiles.some((file) => file.name === value)

const resolveXcodeAgentMarkdownFileName = (value: string): XcodeAgentMarkdownFileName => {
  if (isXcodeAgentMarkdownFileName(value)) {
    return value
  }

  throw new Error(`Unsupported XcodeAgent markdown file: ${value}`)
}

const getMarkdownFileConfig = (
  fileName: XcodeAgentMarkdownFileName
): { name: XcodeAgentMarkdownFileName; enabled: boolean; defaultContent: string } => {
  const fileConfig = markdownFiles.find((file) => file.name === fileName)

  if (!fileConfig) {
    throw new Error(`Unsupported XcodeAgent markdown file: ${fileName}`)
  }

  return fileConfig
}

export const getXcodeAgentHomePath = (): string =>
  join(app.getPath('home'), XCODEAGENT_HOME_DIR_NAME)

export const getXcodeAgentMarkdownFilePath = (fileName: XcodeAgentMarkdownFileName): string =>
  join(getXcodeAgentHomePath(), resolveXcodeAgentMarkdownFileName(fileName))

export const getAgentsFilePath = (): string => getXcodeAgentMarkdownFilePath(AGENTS_FILE_NAME)

export const ensureXcodeAgentHome = async (): Promise<void> => {
  const homePath = getXcodeAgentHomePath()

  await mkdir(homePath, { recursive: true })

  await Promise.all(
    markdownFiles.map(async (file) => {
      try {
        await writeFile(getXcodeAgentMarkdownFilePath(file.name), file.defaultContent, {
          encoding: 'utf-8',
          flag: 'wx'
        })
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') {
          throw error
        }
      }
    })
  )
}

const buildMarkdownFileSummary = async (
  fileName: XcodeAgentMarkdownFileName
): Promise<XcodeAgentMarkdownFileSummary> => {
  const fileConfig = getMarkdownFileConfig(fileName)
  const filePath = getXcodeAgentMarkdownFilePath(fileName)
  const fileStat = await stat(filePath)

  return {
    name: fileName,
    path: filePath,
    size: fileStat.size,
    updatedAt: fileStat.mtime.toISOString(),
    enabled: fileConfig.enabled
  }
}

export const listXcodeAgentMarkdownFiles = async (): Promise<XcodeAgentMarkdownFileSummary[]> => {
  await ensureXcodeAgentHome()

  return Promise.all(markdownFiles.map((file) => buildMarkdownFileSummary(file.name)))
}

export const readXcodeAgentMarkdownFile = async (
  fileName: XcodeAgentMarkdownFileName
): Promise<XcodeAgentMarkdownFileContent> => {
  await ensureXcodeAgentHome()

  const resolvedFileName = resolveXcodeAgentMarkdownFileName(fileName)
  const filePath = getXcodeAgentMarkdownFilePath(resolvedFileName)
  const [content, fileStat] = await Promise.all([readFile(filePath, 'utf-8'), stat(filePath)])
  const fileConfig = getMarkdownFileConfig(resolvedFileName)

  return {
    name: resolvedFileName,
    path: filePath,
    size: fileStat.size,
    updatedAt: fileStat.mtime.toISOString(),
    enabled: fileConfig.enabled,
    content
  }
}

export const saveXcodeAgentMarkdownFile = async (
  fileName: XcodeAgentMarkdownFileName,
  content: string
): Promise<XcodeAgentMarkdownFileContent> => {
  if (typeof content !== 'string') {
    throw new Error('XcodeAgent markdown file content must be a string')
  }

  await ensureXcodeAgentHome()

  const resolvedFileName = resolveXcodeAgentMarkdownFileName(fileName)
  const filePath = getXcodeAgentMarkdownFilePath(resolvedFileName)

  await writeFile(filePath, content, 'utf-8')

  return readXcodeAgentMarkdownFile(resolvedFileName)
}

export const readAgentsFile = async (): Promise<AgentsFileContent> =>
  readXcodeAgentMarkdownFile(AGENTS_FILE_NAME)

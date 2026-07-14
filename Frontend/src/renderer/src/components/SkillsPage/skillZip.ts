import JSZip, { type JSZipObject } from 'jszip'
import { parseSkillFrontmatter } from './skillFrontmatter'

const MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
const MAX_EXPANDED_BYTES = 32 * 1024 * 1024
const MAX_FILE_BYTES = 10 * 1024 * 1024
const MAX_FILE_COUNT = 256
const MAX_ENTRY_COUNT = 512
const MAX_SKILL_CONTENT_BYTES = 512 * 1024
const SKILL_NAME_PATTERN = /^[a-z][a-z0-9_-]*$/

export type SkillZipPreview = {
  archiveBase64: string
  description: string
  fileCount: number
  fileName: string
  name: string
  skillRoot: string
  totalUncompressedBytes: number
}

export async function inspectSkillZip(
  archiveBytes: ArrayBuffer,
  fileName: string
): Promise<SkillZipPreview> {
  if (!fileName.toLocaleLowerCase().endsWith('.zip')) {
    throw new Error('请选择 ZIP 格式的技能包。')
  }
  if (archiveBytes.byteLength === 0) throw new Error('ZIP 文件为空。')
  if (archiveBytes.byteLength > MAX_ARCHIVE_BYTES) throw new Error('ZIP 文件不能超过 32 MiB。')

  let archive: JSZip
  try {
    archive = await JSZip.loadAsync(archiveBytes, { createFolders: false })
  } catch {
    throw new Error('ZIP 文件损坏、已加密或格式不受支持。')
  }

  const entries = Object.values(archive.files) as JSZipObject[]
  if (entries.length > MAX_ENTRY_COUNT) {
    throw new Error(`ZIP 中的文件和目录总数不能超过 ${MAX_ENTRY_COUNT} 个。`)
  }
  for (const entry of entries) {
    assertSafeArchivePath(entry.name, entry.unsafeOriginalName, entry.dir)
    if (isSymbolicLink(entry.unixPermissions)) {
      throw new Error(`ZIP 中不允许包含符号链接：${entry.name}`)
    }
  }
  const contentEntries = entries.filter((entry) => !isIgnoredMetadataPath(entry.name))
  const files = contentEntries.filter((entry) => !entry.dir)
  if (files.length === 0) throw new Error('ZIP 中没有可导入的文件。')
  if (files.length > MAX_FILE_COUNT) throw new Error(`ZIP 中的文件不能超过 ${MAX_FILE_COUNT} 个。`)
  assertNoCaseInsensitivePathConflicts(contentEntries)
  assertDeclaredSizeLimits(files)
  try {
    await JSZip.loadAsync(archiveBytes, { checkCRC32: true, createFolders: false })
  } catch {
    throw new Error('ZIP 文件 CRC 校验失败、已加密或格式不受支持。')
  }

  const skillFiles = files.filter((entry) => /(^|\/)SKILL\.md$/.test(entry.name))
  if (skillFiles.length !== 1) {
    throw new Error('ZIP 必须且只能包含一个 SKILL.md。')
  }
  const skillFile = skillFiles[0]
  const skillRoot = skillFile.name.slice(0, -'SKILL.md'.length).replace(/\/$/, '')
  if (skillRoot && skillRoot.includes('/')) {
    throw new Error('SKILL.md 只能位于 ZIP 根目录或唯一的顶层技能目录中。')
  }
  if (contentEntries.some((entry) => !isWithinSkillRoot(entry.name, skillRoot))) {
    throw new Error('ZIP 中的所有文件必须位于同一个技能目录中。')
  }

  const skillContentBytes = await skillFile.async('uint8array')
  if (skillContentBytes.byteLength > MAX_SKILL_CONTENT_BYTES) {
    throw new Error('SKILL.md 不能超过 512 KiB。')
  }
  const skillContent = decodeUtf8(skillContentBytes)
  const metadata = parseSkillFrontmatter(skillContent)
  assertValidSkillName(metadata.name)

  let totalUncompressedBytes = 0
  for (const entry of files) {
    const content = await entry.async('uint8array')
    if (content.byteLength > MAX_FILE_BYTES) {
      throw new Error(`ZIP 中的单个资源文件不能超过 10 MiB：${entry.name}`)
    }
    totalUncompressedBytes += content.byteLength
    if (totalUncompressedBytes > MAX_EXPANDED_BYTES) {
      throw new Error('ZIP 解压后的文件总大小不能超过 32 MiB。')
    }
  }

  return {
    archiveBase64: bytesToBase64(new Uint8Array(archiveBytes)),
    description: metadata.description,
    fileCount: files.length,
    fileName,
    name: metadata.name,
    skillRoot,
    totalUncompressedBytes
  }
}

function assertSafeArchivePath(
  path: string,
  unsafeOriginalName: string | undefined,
  directory: boolean
): void {
  const originalPath = unsafeOriginalName || path
  const pathWithoutDirectorySlash = directory ? originalPath.replace(/\/$/, '') : originalPath
  if (
    !pathWithoutDirectorySlash ||
    originalPath.includes('\0') ||
    originalPath.includes('\\') ||
    originalPath.startsWith('/') ||
    /^[a-zA-Z]:\//.test(originalPath) ||
    pathWithoutDirectorySlash.split('/').some((part) => part === '..' || part === '')
  ) {
    throw new Error(`ZIP 中包含不安全的文件路径：${originalPath}`)
  }
}

function isIgnoredMetadataPath(path: string): boolean {
  const parts = path.split('/')
  return parts.includes('__MACOSX') || parts.at(-1) === '.DS_Store'
}

function assertDeclaredSizeLimits(files: JSZipObject[]): void {
  let totalBytes = 0
  for (const entry of files) {
    const uncompressedBytes = (entry as JSZipObject & { _data?: { uncompressedSize?: unknown } })
      ._data?.uncompressedSize
    if (
      typeof uncompressedBytes !== 'number' ||
      !Number.isSafeInteger(uncompressedBytes) ||
      uncompressedBytes < 0
    ) {
      throw new Error(`ZIP 无法确定资源文件大小：${entry.name}`)
    }
    const maxBytes = /(^|\/)SKILL\.md$/.test(entry.name) ? MAX_SKILL_CONTENT_BYTES : MAX_FILE_BYTES
    if (uncompressedBytes > maxBytes) {
      throw new Error(
        /(^|\/)SKILL\.md$/.test(entry.name)
          ? 'SKILL.md 不能超过 512 KiB。'
          : `ZIP 中的单个资源文件不能超过 10 MiB：${entry.name}`
      )
    }
    totalBytes += uncompressedBytes
    if (totalBytes > MAX_EXPANDED_BYTES) {
      throw new Error('ZIP 解压后的文件总大小不能超过 32 MiB。')
    }
  }
}

function assertNoCaseInsensitivePathConflicts(files: JSZipObject[]): void {
  const seenPaths = new Map<string, string>()
  for (const entry of files) {
    const parts = entry.name.replace(/\/$/, '').split('/')
    for (let index = 1; index <= parts.length; index += 1) {
      const original = parts.slice(0, index).join('/')
      const normalized = original.toLocaleLowerCase('en-US')
      const existing = seenPaths.get(normalized)
      if (existing && existing !== original) {
        throw new Error(`ZIP 中包含大小写冲突的文件路径：${existing} 与 ${original}`)
      }
      seenPaths.set(normalized, original)
    }
  }
}

function isSymbolicLink(permission: string | number | null | undefined): boolean {
  const mode =
    typeof permission === 'number'
      ? permission
      : typeof permission === 'string' && /^[0-7]+$/.test(permission)
        ? Number.parseInt(permission, 8)
        : undefined
  return mode !== undefined && (mode & 0o170000) === 0o120000
}

function isWithinSkillRoot(path: string, skillRoot: string): boolean {
  if (!skillRoot) return true
  return path === `${skillRoot}/` || path.startsWith(`${skillRoot}/`)
}

function decodeUtf8(content: Uint8Array): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(content).replace(/^\uFEFF/, '')
  } catch {
    throw new Error('SKILL.md 必须使用 UTF-8 编码。')
  }
}

function assertValidSkillName(name: string): void {
  if (!SKILL_NAME_PATTERN.test(name)) {
    throw new Error('name 必须以英文小写字母开头，且仅包含小写字母、数字、下划线和连字符。')
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  const chunkSize = 0x8000
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return globalThis.btoa(binary)
}

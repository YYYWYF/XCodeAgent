import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'

export const MAX_REQUIREMENT_SCREENSHOTS = 10
export const MAX_REQUIREMENT_SCREENSHOT_BYTES = 15 * 1024 * 1024

type ScreenshotMimeType = 'image/jpeg' | 'image/png' | 'image/webp'
type ScreenshotExtension = '.jpg' | '.png' | '.webp'

type ValidatedRequirementScreenshot = {
  sourcePath: string
  name: string
  size: number
  mimeType: ScreenshotMimeType
  sha256: string
  extension: ScreenshotExtension
  content: Buffer
}

export type RequirementScreenshotSelection = {
  path: string
  name: string
  size: number
}

export type StagedRequirementScreenshot = {
  relativePath: string
  name: string
  size: number
  mimeType: ScreenshotMimeType
  sha256: string
}

export type WorkspaceRequirementInput = {
  mode: 'text' | 'screenshot'
  screenshots: StagedRequirementScreenshot[]
}

/** 根据文件头识别允许的截图格式，拒绝伪造扩展名或 MIME。 */
function screenshotFormat(
  content: Buffer
): { mimeType: ScreenshotMimeType; extension: ScreenshotExtension } | undefined {
  if (
    content.length >= 8 &&
    content.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  ) {
    return { mimeType: 'image/png', extension: '.png' }
  }
  if (
    content.length >= 3 &&
    content[0] === 0xff &&
    content[1] === 0xd8 &&
    content[2] === 0xff
  ) {
    return { mimeType: 'image/jpeg', extension: '.jpg' }
  }
  if (
    content.length >= 12 &&
    content.subarray(0, 4).toString('ascii') === 'RIFF' &&
    content.subarray(8, 12).toString('ascii') === 'WEBP'
  ) {
    return { mimeType: 'image/webp', extension: '.webp' }
  }
  return undefined
}

/** 判断文件读取异常是否表示目标不存在。 */
function isMissingFile(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | undefined)?.code === 'ENOENT'
}

/** 校验截图数量、绝对路径、普通文件属性、真实格式、大小和内容摘要。 */
async function validateScreenshotPaths(values: unknown): Promise<ValidatedRequirementScreenshot[]> {
  if (values === undefined) return []
  if (!Array.isArray(values) || values.length > MAX_REQUIREMENT_SCREENSHOTS) {
    throw new Error(`参考截图必须是数组且不能超过 ${MAX_REQUIREMENT_SCREENSHOTS} 张`)
  }
  const uniquePaths = new Set<string>()
  const result: ValidatedRequirementScreenshot[] = []
  for (const value of values) {
    if (typeof value !== 'string' || !value.trim() || !path.isAbsolute(value)) {
      throw new Error('参考截图必须使用有效的绝对路径')
    }
    const sourcePath = path.resolve(value)
    const pathKey = process.platform === 'win32' ? sourcePath.toLowerCase() : sourcePath
    if (uniquePaths.has(pathKey)) continue
    uniquePaths.add(pathKey)
    const stat = await fs.lstat(sourcePath)
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error(`参考截图不是普通文件：${sourcePath}`)
    }
    if (stat.size <= 0 || stat.size > MAX_REQUIREMENT_SCREENSHOT_BYTES) {
      throw new Error(`参考截图大小必须在 1 字节到 15 MB 之间：${path.basename(sourcePath)}`)
    }
    const content = await fs.readFile(sourcePath)
    const format = screenshotFormat(content)
    if (!format) {
      throw new Error(`仅支持真实的 JPG、PNG 或 WebP 图片：${path.basename(sourcePath)}`)
    }
    result.push({
      sourcePath,
      name: path.basename(sourcePath),
      size: content.length,
      mimeType: format.mimeType,
      extension: format.extension,
      sha256: crypto.createHash('sha256').update(content).digest('hex'),
      content
    })
  }
  return result
}

/** 校验文件选择器返回的截图并投射为 Renderer 可展示的安全摘要。 */
export async function selectRequirementScreenshotMetadata(
  values: unknown
): Promise<RequirementScreenshotSelection[]> {
  const screenshots = await validateScreenshotPaths(values)
  return screenshots.map(({ sourcePath: screenshotPath, name, size }) => ({
    path: screenshotPath,
    name,
    size
  }))
}

/** 将截图原子来源字节写入新工作区，并持久化唯一需求输入清单。 */
export async function stageWorkspaceRequirementInput(
  workspaceRoot: string,
  values: unknown
): Promise<WorkspaceRequirementInput> {
  const screenshots = await validateScreenshotPaths(values)
  if (!screenshots.length) return { mode: 'text', screenshots: [] }

  const screenshotRoot = path.join(workspaceRoot, '.xcodeagent', 'inputs', 'screenshots')
  const batchId = crypto.randomUUID()
  const destinationRoot = path.join(screenshotRoot, batchId)
  await fs.mkdir(destinationRoot, { recursive: true })
  const staged: StagedRequirementScreenshot[] = []
  for (const [index, screenshot] of screenshots.entries()) {
    const destinationName = `${String(index + 1).padStart(2, '0')}-${screenshot.sha256.slice(0, 12)}${screenshot.extension}`
    const destinationPath = path.join(destinationRoot, destinationName)
    await fs.writeFile(destinationPath, screenshot.content, { flag: 'wx' })
    staged.push({
      relativePath: path.relative(workspaceRoot, destinationPath).split(path.sep).join('/'),
      name: screenshot.name,
      mimeType: screenshot.mimeType,
      size: screenshot.size,
      sha256: screenshot.sha256
    })
  }
  const requirementInput: WorkspaceRequirementInput = {
    mode: 'screenshot',
    screenshots: staged
  }
  await fs.writeFile(
    path.join(screenshotRoot, 'requirement-input.json'),
    `${JSON.stringify(requirementInput, null, 2)}\n`,
    { encoding: 'utf8', flag: 'wx' }
  )
  return requirementInput
}

/** 按真实路径确保清单截图和截图根目录都没有越出当前工作区。 */
async function resolveStoredScreenshotPath(
  workspaceRoot: string,
  relativePath: string
): Promise<string> {
  const resolvedWorkspaceRoot = path.resolve(workspaceRoot)
  const screenshotRoot = path.resolve(workspaceRoot, '.xcodeagent', 'inputs', 'screenshots')
  const candidate = path.resolve(workspaceRoot, relativePath)
  const candidateStat = await fs.lstat(candidate)
  if (!candidateStat.isFile() || candidateStat.isSymbolicLink()) {
    throw new Error('需求截图不是普通文件')
  }
  const [realWorkspaceRoot, realScreenshotRoot, realCandidate] = await Promise.all([
    fs.realpath(resolvedWorkspaceRoot),
    fs.realpath(screenshotRoot),
    fs.realpath(candidate)
  ])
  const screenshotRootRelative = path.relative(realWorkspaceRoot, realScreenshotRoot)
  if (
    !screenshotRootRelative ||
    screenshotRootRelative.startsWith('..') ||
    path.isAbsolute(screenshotRootRelative)
  ) {
    throw new Error('需求截图目录超出工作区')
  }
  const relative = path.relative(realScreenshotRoot, realCandidate)
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('需求截图清单包含越界路径')
  }
  return realCandidate
}

/** 读取并复核工作区需求截图清单，防止恢复时发送损坏或被替换的图片。 */
export async function readWorkspaceRequirementInput(
  workspaceRoot: string
): Promise<WorkspaceRequirementInput | undefined> {
  const manifestPath = path.join(
    workspaceRoot,
    '.xcodeagent',
    'inputs',
    'screenshots',
    'requirement-input.json'
  )
  let raw: string
  try {
    const stat = await fs.lstat(manifestPath)
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('需求截图清单不是普通文件')
    raw = await fs.readFile(manifestPath, 'utf8')
  } catch (error) {
    if (isMissingFile(error)) return undefined
    throw error
  }
  const parsed = JSON.parse(raw) as Partial<WorkspaceRequirementInput>
  if (
    parsed.mode !== 'screenshot' ||
    !Array.isArray(parsed.screenshots) ||
    !parsed.screenshots.length ||
    parsed.screenshots.length > MAX_REQUIREMENT_SCREENSHOTS
  ) {
    throw new Error('需求截图清单格式无效')
  }
  const verified: StagedRequirementScreenshot[] = []
  for (const value of parsed.screenshots) {
    if (!value || typeof value !== 'object') throw new Error('需求截图清单条目无效')
    const relativePath = String(value.relativePath || '')
    const candidate = await resolveStoredScreenshotPath(workspaceRoot, relativePath)
    const stat = await fs.lstat(candidate)
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('需求截图不是普通文件')
    const content = await fs.readFile(candidate)
    const format = screenshotFormat(content)
    const sha256 = crypto.createHash('sha256').update(content).digest('hex')
    if (
      !format ||
      format.mimeType !== value.mimeType ||
      content.length !== value.size ||
      sha256 !== value.sha256
    ) {
      throw new Error(`需求截图校验失败：${String(value.name || relativePath)}`)
    }
    verified.push({
      relativePath,
      name: String(value.name || path.basename(candidate)),
      mimeType: format.mimeType,
      size: content.length,
      sha256
    })
  }
  return { mode: 'screenshot', screenshots: verified }
}
